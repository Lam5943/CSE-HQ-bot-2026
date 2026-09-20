import asyncio

from cse_hq_bot.models import Actor, Role
from cse_hq_bot.services.ai_service import AIService


class QAService:
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service

    def ask(self, question: str) -> str:
        answer = asyncio.run(
            self.ai_service.answer_question(
                actor=Actor("qa", Role.LEADER),
                question=question,
                history_messages=[],
            )
        )
        return answer.content
