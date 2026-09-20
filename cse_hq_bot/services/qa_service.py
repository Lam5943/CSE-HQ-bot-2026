import asyncio

from cse_hq_bot.models import Actor, Role
from cse_hq_bot.services.ai_service import AIService


class QAService:
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service

    async def aask(self, question: str) -> str:
        answer = await self.ai_service.answer_question(
            actor=Actor("qa", Role.LEADER),
            question=question,
            history_messages=[],
        )
        return answer.content

    def ask(self, question: str) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aask(question))
        raise RuntimeError("QAService.ask() cannot run inside an active event loop; use aask() instead")
