import asyncio
import base64
import logging

from cse_hq_bot.ai.base import (
    AIImage,
    AIMessage,
    AIProviderResponse,
    RetrievedContextRecord,
)
from cse_hq_bot.ai.prompt_renderer import render_provider_prompt
from cse_hq_bot.errors import (
    AIConfigurationError,
    AIMalformedResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)

try:
    import openai
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    openai = None
    AsyncOpenAI = None

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider:
    def __init__(self, api_key: str | None, model_name: str | None):
        if not api_key:
            raise AIConfigurationError(
                "GROQ_API_KEY is required when AI_PROVIDER=groq"
            )
        if not model_name:
            raise AIConfigurationError(
                "GROQ_MODEL is required when AI_PROVIDER=groq"
            )
        if openai is None or AsyncOpenAI is None:
            raise AIConfigurationError("openai package is not available")
        self.model_name = model_name
        self.last_result = "unknown"
        self.last_error_category: str | None = None
        try:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=GROQ_BASE_URL,
                max_retries=0,
            )
        except Exception as exc:  # pragma: no cover - SDK boundary
            raise self._normalize_error(exc) from exc

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
        timeout_seconds: int,
        images: list[AIImage] | None = None,
    ) -> AIProviderResponse:
        prompt = render_provider_prompt(system_instruction, messages, context_records)
        request_input: str | list[dict] = prompt
        if images:
            request_input = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                        *[
                            {
                                "type": "input_image",
                                "detail": "auto",
                                "image_url": self._data_uri(image),
                            }
                            for image in images
                        ],
                    ],
                }
            ]

        try:
            client = self.client.with_options(timeout=timeout_seconds, max_retries=0)
            response = await asyncio.wait_for(
                client.responses.create(
                    model=self.model_name,
                    input=request_input,
                    store=False,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            error = AITimeoutError("Groq request timed out")
            self._record_failure(error)
            raise error from exc
        except Exception as exc:  # pragma: no cover - SDK boundary
            error = self._normalize_error(exc)
            self._record_failure(error)
            raise error from exc

        try:
            text = self._extract_text(response)
        except AIProviderError as error:
            self._record_failure(error)
            raise

        self.last_result = "success"
        self.last_error_category = None
        return AIProviderResponse(
            text=text,
            provider="groq",
            model=self.model_name,
        )

    def health_snapshot(self) -> dict:
        return {
            "primary_result": self.last_result,
            "primary_error_category": self.last_error_category,
        }

    @staticmethod
    def _data_uri(image: AIImage) -> str:
        encoded = base64.b64encode(image.data).decode("ascii")
        return f"data:{image.mime_type};base64,{encoded}"

    def _record_failure(self, error: AIProviderError) -> None:
        self.last_result = "failed"
        self.last_error_category = error.__class__.__name__
        logger.warning(
            "AI provider request failed",
            extra={
                "component": "ai_groq",
                "operation": "generate",
                "result": "failed",
                "error_category": error.__class__.__name__,
            },
        )

    def _extract_text(self, response: object) -> str:
        if response is None:
            raise AIMalformedResponseError("Groq returned an empty response")
        try:
            direct_text = getattr(response, "output_text", None)
        except Exception:  # noqa: BLE001 - SDK response properties may raise arbitrary errors
            direct_text = None
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()

        parts: list[str] = []
        try:
            for item in getattr(response, "output", None) or []:
                for content in getattr(item, "content", None) or []:
                    text = getattr(content, "text", None)
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
        except Exception:  # noqa: BLE001 - malformed SDK response shapes are normalized
            parts = []
        if parts:
            return "\n".join(parts)
        raise AIMalformedResponseError("Groq returned an empty or malformed response")

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            headers = getattr(exc, "headers", None)
        if headers is None:
            return None
        try:
            raw = headers.get("retry-after")
        except Exception:  # pragma: no cover - SDK/header mapping boundary
            return None
        if raw is None:
            return None
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return max(0.0, value)

    def _normalize_error(self, exc: Exception) -> AIProviderError:
        name = exc.__class__.__name__.lower()
        message = str(exc).lower()
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        code = getattr(exc, "code", None)

        if status in {401, 403, 404} or any(
            marker in name
            for marker in ("authentication", "permissiondenied", "notfound")
        ):
            return AIConfigurationError("Groq configuration is invalid")
        if status == 429 or "ratelimit" in name or "rate limit" in message:
            return AIRateLimitError(
                "Groq rate limit exceeded",
                retry_after_seconds=self._retry_after_seconds(exc),
            )
        if "timeout" in name or "timed out" in message:
            return AITimeoutError("Groq request timed out")
        if (
            isinstance(status, int)
            and status >= 500
            or "apiconnection" in name
            or "internalserver" in name
            or "service unavailable" in message
            or code == "server_is_overloaded"
        ):
            return AIProviderUnavailableError("Groq is temporarily unavailable")
        return AIProviderError("Groq request failed")
