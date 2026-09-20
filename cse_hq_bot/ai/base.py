from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AIMessage:
    role: str
    content: str


@dataclass(frozen=True)
class RetrievedContextRecord:
    source_type: str
    source_id: str
    title: str
    content: str
    timestamp: str | None
    retrieval_reason: str


@dataclass(frozen=True)
class AIProviderResponse:
    text: str
    provider: str | None = None
    model: str | None = None
    fallback_used: bool = False


class AIProvider(Protocol):
    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
        timeout_seconds: int,
    ) -> AIProviderResponse:
        """Return an answer for a bounded, grounded prompt."""
