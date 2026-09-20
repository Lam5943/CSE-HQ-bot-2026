from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cse_hq_bot.ai.action_models import (
    ActionExecutionResult,
    ActionProposal,
    ActionProposalDraft,
    ActionProposalStatus,
)
from cse_hq_bot.errors import (
    AIActionAlreadyHandledError,
    AIActionConflictError,
    AIActionExpiredError,
    AIActionValidationError,
    AISessionClosedError,
    CSEHQError,
    InvalidTransitionError,
    PermissionDeniedError,
)
from cse_hq_bot.models import Actor
from cse_hq_bot.repositories.ai_action_repository import AIActionProposalRepository
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.services.ai_action_registry import AIActionRegistry


class AIActionService:
    def __init__(
        self,
        repo: AIActionProposalRepository,
        session_repo: AISessionRepository,
        registry: AIActionRegistry,
        *,
        expiration_seconds: int = 600,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repo = repo
        self.session_repo = session_repo
        self.registry = registry
        self.expiration_seconds = max(60, int(expiration_seconds))
        self.clock = clock or (lambda: datetime.now(UTC))

    def create_proposal(
        self,
        *,
        actor: Actor,
        session_id: int,
        source_message_id: int | None,
        draft: ActionProposalDraft,
    ) -> ActionProposal:
        session = self.session_repo.get_session(session_id)
        self._validate_session(actor, session)
        now = self.clock()
        expires = now + timedelta(seconds=self.expiration_seconds)
        proposal_id = self.repo.create(
            session_id=session_id,
            actor_id=actor.user_id,
            draft=draft,
            source_message_id=source_message_id,
            created_at=self._timestamp(now),
            expires_at=self._timestamp(expires),
        )
        return self.repo.get(proposal_id)

    def get_proposal(self, actor: Actor, proposal_id: int) -> ActionProposal:
        proposal = self._refresh_expiration(proposal_id)
        self._validate_owner(actor, proposal)
        return proposal

    def cancel(self, actor: Actor, proposal_id: int) -> ActionExecutionResult:
        proposal = self._refresh_expiration(proposal_id)
        self._validate_owner(actor, proposal)
        self._require_pending(proposal)
        cancelled = self.repo.cancel_pending(
            proposal_id, actor.user_id, self._now_timestamp()
        )
        if not cancelled:
            self._raise_current_state(actor, proposal_id)
        updated = self.repo.get(proposal_id)
        return ActionExecutionResult(updated, "❌ Action cancelled. No project data changed.")

    def confirm(self, actor: Actor, proposal_id: int) -> ActionExecutionResult:
        proposal = self._refresh_expiration(proposal_id)
        self._validate_owner(actor, proposal)
        self._require_pending(proposal)
        session = self.session_repo.get_session(proposal.session_id)
        self._validate_session(actor, session)
        claimed = self.repo.claim_for_confirmation(
            proposal_id, actor.user_id, self._now_timestamp()
        )
        if not claimed:
            self._raise_current_state(actor, proposal_id)

        claimed_proposal = self.repo.get(proposal_id)
        try:
            session = self.session_repo.get_session(claimed_proposal.session_id)
            self._validate_session(actor, session)
            message = self.registry.execute(claimed_proposal, actor)
        except AIActionConflictError:
            self.repo.mark_failed(proposal_id, "STALE_STATE")
            raise
        except InvalidTransitionError as exc:
            self.repo.mark_failed(proposal_id, "STALE_STATE")
            raise AIActionConflictError(
                "The target changed after this action was proposed"
            ) from exc
        except CSEHQError as exc:
            self.repo.mark_failed(proposal_id, exc.__class__.__name__)
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            self.repo.mark_failed(proposal_id, exc.__class__.__name__)
            raise AIActionValidationError("The action could not be executed safely") from exc
        self.repo.mark_executed(proposal_id, self._now_timestamp())
        return ActionExecutionResult(self.repo.get(proposal_id), message)

    def invalidate_session(self, session_id: int) -> int:
        return self.repo.fail_pending_for_session(session_id)

    def _refresh_expiration(self, proposal_id: int) -> ActionProposal:
        self.repo.expire_pending(proposal_id, self._now_timestamp())
        return self.repo.get(proposal_id)

    def _raise_current_state(self, actor: Actor, proposal_id: int) -> None:
        current = self._refresh_expiration(proposal_id)
        self._validate_owner(actor, current)
        if current.status == ActionProposalStatus.EXPIRED.value:
            raise AIActionExpiredError("This AI action proposal has expired")
        if current.status != ActionProposalStatus.PENDING.value:
            raise AIActionAlreadyHandledError(
                f"This AI action proposal is already {current.status.lower()}"
            )
        session = self.session_repo.get_session(current.session_id)
        self._validate_session(actor, session)
        raise AIActionConflictError("The AI action proposal could not be claimed")

    def _require_pending(self, proposal: ActionProposal) -> None:
        if proposal.status == ActionProposalStatus.EXPIRED.value:
            raise AIActionExpiredError("This AI action proposal has expired")
        if proposal.status != ActionProposalStatus.PENDING.value:
            raise AIActionAlreadyHandledError(
                f"This AI action proposal is already {proposal.status.lower()}"
            )

    def _validate_owner(self, actor: Actor, proposal: ActionProposal) -> None:
        if proposal.actor_id != actor.user_id:
            raise PermissionDeniedError(
                "Only the proposal creator may handle this AI action"
            )

    def _validate_session(self, actor: Actor, session: dict) -> None:
        if session["owner_id"] != actor.user_id:
            raise PermissionDeniedError("You do not own this AI session")
        if session["status"] != "ACTIVE":
            raise AISessionClosedError(
                "Closed AI sessions cannot execute action proposals"
            )

    def _now_timestamp(self) -> str:
        return self._timestamp(self.clock())

    def _timestamp(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="seconds")
