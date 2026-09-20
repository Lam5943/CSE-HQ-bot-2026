import asyncio

from cse_hq_bot.ai.base import AIMessage, AIProviderResponse, RetrievedContextRecord
from cse_hq_bot.errors import (
    AIConfigurationError,
    AIMalformedResponseError,
    AIProviderError,
    AIRateLimitError,
    AITimeoutError,
)

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover
    genai = None


class GeminiProvider:
    def __init__(self, api_key: str | None, model_name: str):
        if not api_key:
            raise AIConfigurationError("GEMINI_API_KEY is required when AI_PROVIDER=gemini")
        if genai is None:
            raise AIConfigurationError("google-generativeai package is not available")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
        timeout_seconds: int,
    ) -> AIProviderResponse:
        prompt = self._build_prompt(system_instruction, messages, context_records)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self.model.generate_content, prompt),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise AITimeoutError("The AI provider timed out") from exc
        except Exception as exc:  # pragma: no cover - depends on SDK/runtime
            raise self._normalize_error(exc) from exc
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise AIMalformedResponseError("The AI provider returned an empty response")
        return AIProviderResponse(text=text)

    def _build_prompt(
        self,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
    ) -> str:
        conversation = "\n".join(f"{message.role.upper()}: {message.content}" for message in messages)
        context = "\n".join(
            (
                f"[{record.source_id}] type={record.source_type} title={record.title} "
                f"timestamp={record.timestamp or 'unknown'} reason={record.retrieval_reason}\n"
                f"{record.content}"
            )
            for record in context_records
        )
        return (
            f"<SYSTEM_INSTRUCTIONS>\n{system_instruction}\n</SYSTEM_INSTRUCTIONS>\n\n"
            f"<PROJECT_CONTEXT>\n{context or 'No matching project records were retrieved.'}\n</PROJECT_CONTEXT>\n\n"
            f"<CONVERSATION>\n{conversation}\n</CONVERSATION>"
        )

    def _normalize_error(self, exc: Exception) -> AIProviderError:
        message = str(exc).lower()
        name = exc.__class__.__name__.lower()
        if "api key" in message or "authentication" in message or "permission denied" in message:
            return AIConfigurationError("Gemini configuration is invalid")
        if "429" in message or "rate" in message or "resourceexhausted" in name:
            return AIRateLimitError("Gemini rate limit exceeded")
        if "timeout" in message or "deadline" in message:
            return AITimeoutError("Gemini request timed out")
        return AIProviderError("Gemini request failed")
