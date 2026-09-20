from cse_hq_bot.ai.base import (
    AIMessage,
    AIProvider,
    AIProviderResponse,
    RetrievedContextRecord,
)


class FakeAIProvider(AIProvider):
    def __init__(
        self, canned_response: str = "I can only answer using stored project data."
    ):
        self.canned_response = canned_response

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[AIMessage],
        context_records: list[RetrievedContextRecord],
        timeout_seconds: int,
    ) -> AIProviderResponse:
        rendered_messages = "\n".join(
            f"{message.role}: {message.content}" for message in messages
        )
        rendered_context = (
            "\n".join(
                f"- {record.source_id} ({record.source_type}) {record.title}: {record.content}"
                for record in context_records
            )
            or "- none"
        )
        return AIProviderResponse(
            text=(
                f"{self.canned_response}\n\n"
                f"System Instruction:\n{system_instruction}\n\n"
                f"Conversation:\n{rendered_messages}\n\n"
                f"Context Records:\n{rendered_context}"
            ),
            provider="fake",
            model="local-canned-response",
        )
