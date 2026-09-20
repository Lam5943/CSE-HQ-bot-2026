from cse_hq_bot.ai.base import AIProvider


class FakeAIProvider(AIProvider):
    def __init__(self, canned_response: str = "I can only answer using stored project data."):
        self.canned_response = canned_response

    def answer(self, prompt: str) -> str:
        return f"{self.canned_response}\n\nPrompt Context:\n{prompt}"
