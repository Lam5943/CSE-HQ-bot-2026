import logging
import time
from collections import Counter
from dataclasses import replace

from cse_hq_bot.ai.base import (
    AIMessage,
    AIProvider,
    AIProviderResponse,
    RetrievedContextRecord,
)
from cse_hq_bot.errors import (
    AIConfigurationError,
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)

logger = logging.getLogger(__name__)

FAILOVER_ELIGIBLE_ERRORS = (
    AIRateLimitError,
    AITimeoutError,
    AIProviderUnavailableError,
)


class AIProviderRouter:
    def __init__(
        self,
        primary: AIProvider,
        fallback: AIProvider | None,
        *,
        fallback_enabled: bool,
        primary_name: str = "gemini",
        fallback_name: str = "openai",
        fallback_configuration_error: AIConfigurationError | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.fallback_enabled = fallback_enabled
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.fallback_configuration_error = fallback_configuration_error
        self.metrics: Counter[str] = Counter()

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
        timeout_seconds: int,
    ) -> AIProviderResponse:
        started = time.perf_counter()
        try:
            response = await self.primary.generate(
                system_instruction=system_instruction,
                messages=messages,
                context_records=context_records,
                timeout_seconds=timeout_seconds,
            )
        except FAILOVER_ELIGIBLE_ERRORS as primary_error:
            self._record_primary_failure(primary_error)
            if not self.fallback_enabled:
                raise
            self.metrics["fallback_attempt"] += 1
            logger.warning(
                "AI fallback attempt primary=%s fallback=%s failure=%s latency_ms=%d",
                self.primary_name,
                self.fallback_name,
                primary_error.__class__.__name__,
                self._elapsed_ms(started),
            )
            if self.fallback is None:
                self.metrics["fallback_failure"] += 1
                error = self.fallback_configuration_error or AIConfigurationError(
                    "AI fallback provider is not configured"
                )
                logger.error(
                    "AI fallback unavailable provider=%s failure=%s",
                    self.fallback_name,
                    error.__class__.__name__,
                )
                raise error from primary_error
            try:
                fallback_response = await self.fallback.generate(
                    system_instruction=system_instruction,
                    messages=messages,
                    context_records=context_records,
                    timeout_seconds=timeout_seconds,
                )
            except AIProviderError as fallback_error:
                self.metrics["fallback_failure"] += 1
                logger.error(
                    "AI fallback failed primary=%s fallback=%s primary_failure=%s "
                    "fallback_failure=%s latency_ms=%d",
                    self.primary_name,
                    self.fallback_name,
                    primary_error.__class__.__name__,
                    fallback_error.__class__.__name__,
                    self._elapsed_ms(started),
                )
                raise AIProviderUnavailableError(
                    "All configured AI providers are temporarily unavailable"
                ) from fallback_error
            self.metrics["fallback_success"] += 1
            logger.info(
                "AI fallback succeeded provider=%s model=%s latency_ms=%d",
                fallback_response.provider or self.fallback_name,
                fallback_response.model or "unknown",
                self._elapsed_ms(started),
            )
            return replace(fallback_response, fallback_used=True)
        self.metrics["primary_success"] += 1
        logger.info(
            "AI primary succeeded provider=%s model=%s latency_ms=%d",
            response.provider or self.primary_name,
            response.model or "unknown",
            self._elapsed_ms(started),
        )
        return response

    def _record_primary_failure(self, error: AIProviderError) -> None:
        if isinstance(error, AIRateLimitError):
            self.metrics["primary_rate_limit"] += 1
        elif isinstance(error, AITimeoutError):
            self.metrics["primary_timeout"] += 1
        else:
            self.metrics["primary_unavailable"] += 1

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
