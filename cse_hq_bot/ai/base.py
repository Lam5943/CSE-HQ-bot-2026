from typing import Protocol


class AIProvider(Protocol):
    def answer(self, prompt: str) -> str:
        """Return an answer for a grounded prompt."""
