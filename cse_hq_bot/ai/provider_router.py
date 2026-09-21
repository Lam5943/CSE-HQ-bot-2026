import asyncio
import logging
import time
from collections import Counter
from dataclasses import replace

from cse_hq_bot.ai.base import (
    AIImage,
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
        primary_retry_count: int = 0,
        primary_retry_delay_seconds: float = 1.0,
    ):
        self.primary = primary
        self.fallback = fallback
        self.fallback_enabled = fallback_enabled
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.fallback_configuration_error = fallback_configuration_error
        self.primary_retry_count = max(0, int(primary_retry_count))
        self.primary_retry_delay_seconds = max(0.0, float(primary_retry_delay_seconds))
        self.metrics: Counter[str] = Counter()
        self.last_primary_result = "unknown"
        self.last_primary_error_category: str | None = None
        self.last_fallback_result = "unknown"
        self.last_fallback_error_category: str | None = None

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
        timeout_seconds: int,
        images: list[AIImage] | None = None,
    ) -> AIProviderResponse:
        started = time.perf_counter()
        primary_kwargs = {
            "system_instruction": system_instruction,
            "messages": messages,
            "context_records": context_records,
            "timeout_seconds": timeout_seconds,
        }
        if images:
            primary_kwargs["images"] = images
        primary_error: AIProviderError | None = None
        response: AIProviderResponse | None = None
        for attempt in range(self.primary_retry_count + 1):
            try:
                response = await self.primary.generate(**primary_kwargs)
                break
            except AIProviderError as error:
                primary_error = error
                self._record_primary_failure(error)
                retryable = isinstance(
                    error,
                    (AIRateLimitError, AIProviderUnavailableError),
                ) and (not isinstance(error, AIRateLimitError) or error.retryable)
                has_retry = attempt < self.primary_retry_count
                if retryable and has_retry:
                    self.metrics["primary_retry"] += 1
                    logger.warning(
                        "AI primary retry scheduled",
                        extra={
                            "component": "ai_router",
                            "operation": "primary_retry",
                            "result": "retry",
                            "primary_provider": self.primary_name,
                            "attempt": attempt + 1,
                            "error_category": error.__class__.__name__,
                        },
                    )
                    retry_delay = self.primary_retry_delay_seconds * (2**attempt)
                    if isinstance(error, AIRateLimitError):
                        retry_after = error.retry_after_seconds
                        if retry_after is not None:
                            retry_delay = max(retry_delay, retry_after)
                    retry_delay = min(retry_delay, 15.0)
                    if retry_delay:
                        await asyncio.sleep(retry_delay)
                    continue
                break

        if response is None:
            if primary_error is None:  # pragma: no cover - defensive invariant
                raise AIProviderUnavailableError("AI primary provider failed")
            if not isinstance(primary_error, FAILOVER_ELIGIBLE_ERRORS):
                raise primary_error
            if not self.fallback_enabled:
                raise primary_error
            if images:
                self.metrics["fallback_skipped_multimodal"] += 1
                logger.warning(
                    "AI fallback skipped for multimodal request",
                    extra={
                        "component": "ai_router",
                        "operation": "fallback",
                        "result": "skipped_multimodal",
                        "primary_provider": self.primary_name,
                        "fallback_provider": self.fallback_name,
                        "image_count": len(images),
                        "error_category": primary_error.__class__.__name__,
                        "latency_ms": self._elapsed_ms(started),
                    },
                )
                raise primary_error
            self.metrics["fallback_attempt"] += 1
            logger.warning(
                "AI fallback attempt",
                extra={
                    "component": "ai_router",
                    "operation": "fallback",
                    "result": "attempt",
                    "primary_provider": self.primary_name,
                    "fallback_provider": self.fallback_name,
                    "error_category": primary_error.__class__.__name__,
                    "latency_ms": self._elapsed_ms(started),
                },
            )
            if self.fallback is None:
                self.metrics["fallback_failure"] += 1
                error = self.fallback_configuration_error or AIConfigurationError(
                    "AI fallback provider is not configured"
                )
                self.last_fallback_result = "failed"
                self.last_fallback_error_category = error.__class__.__name__
                logger.error(
                    "AI fallback unavailable",
                    extra={
                        "component": "ai_router",
                        "operation": "fallback",
                        "result": "failed",
                        "fallback_provider": self.fallback_name,
                        "error_category": error.__class__.__name__,
                    },
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
                self.last_fallback_result = "failed"
                self.last_fallback_error_category = (
                    fallback_error.__class__.__name__
                )
                logger.error(
                    "AI fallback failed",
                    extra={
                        "component": "ai_router",
                        "operation": "fallback",
                        "result": "failed",
                        "primary_provider": self.primary_name,
                        "fallback_provider": self.fallback_name,
                        "primary_error_category": primary_error.__class__.__name__,
                        "error_category": fallback_error.__class__.__name__,
                        "latency_ms": self._elapsed_ms(started),
                    },
                )
                raise AIProviderUnavailableError(
                    "All configured AI providers are temporarily unavailable"
                ) from fallback_error
            self.metrics["fallback_success"] += 1
            self.last_fallback_result = "success"
            self.last_fallback_error_category = None
            logger.info(
                "AI fallback succeeded",
                extra={
                    "component": "ai_router",
                    "operation": "fallback",
                    "result": "success",
                    "fallback_provider": fallback_response.provider
                    or self.fallback_name,
                    "latency_ms": self._elapsed_ms(started),
                },
            )
            return replace(fallback_response, fallback_used=True)

        self.metrics["primary_success"] += 1
        self.last_primary_result = "success"
        self.last_primary_error_category = None
        logger.info(
            "AI primary succeeded",
            extra={
                "component": "ai_router",
                "operation": "generate",
                "result": "success",
                "primary_provider": response.provider or self.primary_name,
                "latency_ms": self._elapsed_ms(started),
            },
        )
        return response

    def health_snapshot(self) -> dict:
        return {
            "primary_result": self.last_primary_result,
            "primary_error_category": self.last_primary_error_category,
            "fallback_result": self.last_fallback_result,
            "fallback_error_category": self.last_fallback_error_category,
        }

    def _record_primary_failure(self, error: AIProviderError) -> None:
        self.last_primary_result = "failed"
        self.last_primary_error_category = error.__class__.__name__
        if isinstance(error, AIRateLimitError):
            self.metrics["primary_rate_limit"] += 1
        elif isinstance(error, AITimeoutError):
            self.metrics["primary_timeout"] += 1
        else:
            self.metrics["primary_unavailable"] += 1

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
