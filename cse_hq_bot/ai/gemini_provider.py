from cse_hq_bot.errors import AIProviderError

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover
    genai = None


class GeminiAIProvider:
    def __init__(self, api_key: str | None, model_name: str):
        if not api_key:
            raise AIProviderError("GEMINI_API_KEY is required for Gemini provider")
        if genai is None:
            raise AIProviderError("google-generativeai package is not available")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

    def answer(self, prompt: str) -> str:
        try:
            response = self.model.generate_content(prompt)
        except Exception as exc:  # pragma: no cover
            raise AIProviderError("Gemini request failed") from exc
        text = getattr(response, "text", "")
        if not text:
            raise AIProviderError("Gemini returned no text response")
        return text
