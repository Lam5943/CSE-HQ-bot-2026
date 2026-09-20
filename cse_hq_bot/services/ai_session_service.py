import asyncio
from dataclasses import replace

from cse_hq_bot.ai.action_models import KnownMember
from cse_hq_bot.errors import (
    AISessionBusyError,
    AISessionClosedError,
    AISessionConflictError,
    PermissionDeniedError,
)
from cse_hq_bot.models import Actor
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.services.ai_action_service import AIActionService
from cse_hq_bot.services.ai_service import AIService, GroundedAnswer


class AISessionService:
    def __init__(
        self,
        repo: AISessionRepository,
        ai_service: AIService,
        *,
        max_history_messages: int,
        action_service: AIActionService | None = None,
    ):
        self.repo = repo
        self.ai_service = ai_service
        self.max_history_messages = max(1, int(max_history_messages))
        self.action_service = action_service
        self._busy_sessions: set[int] = set()
        self._busy_state_lock = asyncio.Lock()

    def create_session(self, actor: Actor, discord_thread_id: str) -> dict:
        if self.repo.get_session_by_thread_id(discord_thread_id) is not None:
            raise AISessionConflictError(
                "This Discord thread is already linked to another AI session"
            )
        session_id = self.repo.create_session(actor.user_id, discord_thread_id)
        return self.repo.get_session(session_id)

    def list_sessions(self, actor: Actor) -> list[dict]:
        return self.repo.list_sessions_for_owner(actor.user_id)

    def close_session(self, actor: Actor, session_id: int) -> None:
        session = self.repo.get_session(session_id)
        self._validate_session_owner(actor, session)
        if session["status"] != "ACTIVE":
            return
        self.repo.close_session(session_id)
        if self.action_service is not None:
            self.action_service.invalidate_session(session_id)

    def get_session_by_thread_id(self, discord_thread_id: str) -> dict | None:
        return self.repo.get_session_by_thread_id(discord_thread_id)

    def get_session_messages(self, actor: Actor, session_id: int) -> list[dict]:
        session = self.repo.get_session(session_id)
        self._validate_session_access(actor, session, session["discord_thread_id"])
        return self.repo.list_messages(session_id, self.max_history_messages)

    async def handle_message(
        self,
        *,
        actor: Actor,
        session_id: int,
        discord_thread_id: str,
        content: str,
        known_members: list[KnownMember] | None = None,
    ) -> GroundedAnswer:
        session = self.repo.get_session(session_id)
        self._validate_session_access(actor, session, discord_thread_id)
        async with self._busy_state_lock:
            if session_id in self._busy_sessions:
                raise AISessionBusyError("This AI session is already processing a request")
            self._busy_sessions.add(session_id)
        try:
            history = self.repo.list_messages(session_id, self.max_history_messages)
            source_message_id = self.repo.create_message(session_id, "user", content)
            answer_kwargs = {
                "actor": actor,
                "question": content,
                "history_messages": history,
            }
            if known_members is not None:
                answer_kwargs["known_members"] = known_members
            answer = await self.ai_service.answer_question(**answer_kwargs)
            if answer.action_draft is not None:
                if self.action_service is None:
                    raise RuntimeError("AI action service is not configured")
                proposal = self.action_service.create_proposal(
                    actor=actor,
                    session_id=session_id,
                    source_message_id=source_message_id,
                    draft=answer.action_draft,
                )
                answer = replace(answer, action_proposal=proposal)
            self.repo.create_message(session_id, "assistant", answer.content, answer.source_refs)
            return answer
        finally:
            async with self._busy_state_lock:
                self._busy_sessions.discard(session_id)

    def _validate_session_access(self, actor: Actor, session: dict, discord_thread_id: str) -> None:
        if session["status"] != "ACTIVE":
            raise AISessionClosedError("This AI session is closed")
        self._validate_session_owner(actor, session)
        if str(session["discord_thread_id"]) != str(discord_thread_id):
            raise PermissionDeniedError("This message was sent from the wrong AI session thread")

    def _validate_session_owner(self, actor: Actor, session: dict) -> None:
        if session["owner_id"] != actor.user_id:
            raise PermissionDeniedError("You are not allowed to access this AI session")
