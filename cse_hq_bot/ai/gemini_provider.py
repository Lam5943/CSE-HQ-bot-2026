import asyncio

from cse_hq_bot.ai.base import AIMessage, AIProviderResponse, RetrievedContextRecord
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
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = None
    types = None


class GeminiProvider:
    def __init__(self, api_key: str | None, model_name: str):
        if not api_key:
            raise AIConfigurationError(
                "GEMINI_API_KEY is required when AI_PROVIDER=gemini"
            )
        if genai is None or types is None:
            raise AIConfigurationError("google-genai package is not available")
        self.model_name = model_name
        try:
            self.client = genai.Client(api_key=api_key)
        except Exception as exc:  # pragma: no cover - SDK boundary
            raise self._normalize_error(exc) from exc

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
        timeout_seconds: int,
    ) -> AIProviderResponse:
        prompt = render_provider_prompt(system_instruction, messages, context_records)
        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
                    ),
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise AITimeoutError("The AI provider timed out") from exc
        except Exception as exc:  # pragma: no cover - SDK boundary
            raise self._normalize_error(exc) from exc
        return AIProviderResponse(
            text=self._extract_text(response),
            provider="gemini",
            model=self.model_name,
        )

    def _extract_text(self, response: object) -> str:
        if response is None:
            raise AIMalformedResponseError("The AI provider returned an empty response")
        try:
            direct_text = getattr(response, "text", None)
        except Exception:  # noqa: BLE001 - SDK response properties may raise arbitrary errors
            direct_text = None
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()

        parts_text: list[str] = []
        try:
            candidates = getattr(response, "candidates", None) or []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str) and part_text.strip():
                        parts_text.append(part_text.strip())
        except Exception:  # noqa: BLE001 - malformed SDK response shapes are normalized
            parts_text = []
        if parts_text:
            return "\n".join(parts_text)
        raise AIMalformedResponseError(
            "The AI provider returned an empty or malformed response"
        )

    def _normalize_error(self, exc: Exception) -> AIProviderError:
        message = str(exc).lower()
        name = exc.__class__.__name__.lower()
        code = getattr(exc, "code", None)
        if (
            code in {401, 403}
            or "api key" in message
            or "authentication" in message
            or "permission denied" in message
        ):
            return AIConfigurationError("Gemini configuration is invalid")
        if (
            code == 429
            or "429" in message
            or "rate" in message
            or "resourceexhausted" in name
        ):
            return AIRateLimitError("Gemini rate limit exceeded")
        if "timeout" in message or "deadline" in message or "readtimeout" in name:
            return AITimeoutError("Gemini request timed out")
        server_error = isinstance(code, int) and code >= 500
        if (
            server_error
            or "service unavailable" in message
            or "connection" in message
            or "unavailable" in name
        ):
            return AIProviderUnavailableError("Gemini is temporarily unavailable")
        return AIProviderError("Gemini request failed")
