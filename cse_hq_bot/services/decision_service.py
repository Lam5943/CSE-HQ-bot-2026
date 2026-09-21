import logging

from cse_hq_bot.errors import InvalidInputError
from cse_hq_bot.identifiers import decision_code
from cse_hq_bot.models import Actor
from cse_hq_bot.permissions import ensure_can_manage_decisions
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository

logger = logging.getLogger(__name__)


class DecisionService:
    def __init__(self, repo: CollaborationRepository, activity_repo: ActivityRepository | None = None):
        self.repo = repo
        self.activity_repo = activity_repo

    def create_decision(
        self,
        actor: Actor,
        title: str,
        decision: str,
        context: str,
        rationale: str,
        alternatives: str,
        meeting_id: int | None = None,
    ) -> int:
        ensure_can_manage_decisions(actor)
        if meeting_id is not None:
            self.repo.get_meeting(meeting_id)
        decision_id = self.repo.create_decision(
            title=self._require_text(title, "Decision title is required"),
            decision=self._require_text(decision, "Decision text is required"),
            context=context.strip(),
            rationale=rationale.strip(),
            alternatives=alternatives.strip(),
            created_by=actor.user_id,
            meeting_id=meeting_id,
        )
        decision = self.repo.get_decision(decision_id)
        self._append_activity(
            "DECISION_CREATED",
            decision,
            actor.user_id,
            {
                "row_id": decision_id,
                "meeting_id": decision.get("meeting_id"),
            },
        )
        return decision_id

    def list_decisions(self, actor: Actor) -> list[dict]:
        return self.repo.list_decisions()

    def list_accessible_decisions(self, actor: Actor) -> list[dict]:
        return self.list_decisions(actor)

    def get_decision(self, actor: Actor, decision_id: int) -> dict:
        return self.repo.get_decision(decision_id)

    def edit_decision(self, actor: Actor, decision_id: int, **fields: str | int | None) -> None:
        ensure_can_manage_decisions(actor)
        decision = self.repo.get_decision(decision_id)
        valid = {
            key: value
            for key, value in fields.items()
            if key in {"title", "decision", "context", "rationale", "alternatives"} and value is not None
        }
        if "title" in valid:
            valid["title"] = self._require_text(str(valid["title"]), "Decision title is required")
        if "decision" in valid:
            valid["decision"] = self._require_text(str(valid["decision"]), "Decision text is required")
            valid["summary"] = valid["decision"]
        if "title" in valid and "decision" not in valid:
            valid["summary"] = decision["decision"]
        changed_fields = [
            field
            for field in ("title", "decision", "context", "rationale", "alternatives")
            if field in valid and decision.get(field) != valid[field]
        ]
        self.repo.update_decision(decision_id, valid)
        if changed_fields:
            self._append_activity(
                "DECISION_EDITED",
                decision,
                actor.user_id,
                {
                    "row_id": decision_id,
                    "changed_fields": changed_fields,
                },
            )

    def search_decisions(self, actor: Actor, query: str) -> list[dict]:
        needle = self._require_text(query, "Search text is required").lower()
        return [
            decision
            for decision in self.list_decisions(actor)
            if any(
                needle in (decision.get(field) or "").lower()
                for field in ("code", "title", "decision", "context", "rationale", "alternatives")
            )
        ]

    def list_decisions_for_meeting(self, actor: Actor, meeting_id: int) -> list[dict]:
        self.repo.get_meeting(meeting_id)
        return [decision for decision in self.list_decisions(actor) if decision.get("meeting_id") == meeting_id]

    def _require_text(self, value: str, message: str) -> str:
        clean_value = value.strip()
        if not clean_value:
            raise InvalidInputError(message)
        return clean_value

    def _append_activity(self, event_type: str, decision: dict, actor_id: str, metadata: dict) -> None:
        if not self.activity_repo:
            return
        try:
            self.activity_repo.append(
                event_type=event_type,
                entity_type="decision",
                entity_id=decision_code(decision),
                actor_id=actor_id,
                metadata=metadata,
            )
        except Exception:  # pragma: no cover - defensive logging path
            logger.exception(
                "Failed to append decision activity",
                extra={"event_type": event_type, "decision_id": decision["id"]},
            )
