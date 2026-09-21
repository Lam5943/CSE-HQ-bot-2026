import logging
from datetime import UTC, date, datetime

from cse_hq_bot.errors import InvalidInputError
from cse_hq_bot.identifiers import standup_code
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.permissions import ensure_can_view_standup
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository

logger = logging.getLogger(__name__)


class StandupService:
    def __init__(self, repo: CollaborationRepository, activity_repo: ActivityRepository | None = None):
        self.repo = repo
        self.activity_repo = activity_repo

    def submit_standup(
        self,
        actor: Actor,
        previous: str,
        current: str,
        blockers: str = "",
        entry_date: str | None = None,
        *,
        allow_empty_previous: bool = False,
    ) -> int:
        normalized_date = self._normalize_date(entry_date)
        existing = self.repo.get_standup_for_user_date(actor.user_id, normalized_date)
        standup_id = self.repo.create_standup(
            user_id=actor.user_id,
            entry_date=normalized_date,
            previous=(
                previous.strip()
                if allow_empty_previous
                else self._require_text(previous, "Previous update is required")
            ),
            current=self._require_text(current, "Current update is required"),
            blockers=blockers.strip(),
        )
        standup = self.repo.get_standup(standup_id)
        self._append_activity(
            "STANDUP_UPDATED" if existing else "STANDUP_SUBMITTED",
            standup,
            actor.user_id,
            {
                "row_id": standup_id,
                "date": normalized_date,
                "has_blockers": bool(standup.get("blockers")),
                "user_id": standup["user_id"],
            },
        )
        return standup_id

    def get_today(self, actor: Actor, entry_date: str | None = None) -> dict | None:
        return self.repo.get_standup_for_user_date(actor.user_id, self.resolve_entry_date(actor, entry_date))

    def get_user_standup(self, actor: Actor, user_id: str, entry_date: str | None = None) -> dict | None:
        ensure_can_view_standup(actor, user_id)
        subject_actor = actor if actor.user_id == user_id else Actor(user_id, Role.MEMBER)
        return self.repo.get_standup_for_user_date(user_id, self.resolve_entry_date(subject_actor, entry_date))

    def list_for_date(self, actor: Actor, entry_date: str) -> list[dict]:
        target_date = self.resolve_entry_date(actor, entry_date)
        return [standup for standup in self.repo.list_standups() if standup.get("date") == target_date]

    def list_recent(self, actor: Actor, days: int = 7) -> list[dict]:
        return self.repo.list_recent_standups(days=max(days, 1))

    def list_accessible_standups(self, actor: Actor) -> list[dict]:
        standups = self.repo.list_standups()
        if actor.role in {Role.LEADER, Role.CO_LEAD}:
            return standups
        return [standup for standup in standups if standup.get("user_id") == actor.user_id]

    def weekly_standup_summary(self, days: int = 7) -> dict:
        standups = self.repo.list_recent_standups(days=max(days, 1))
        blockers = [entry["blockers"] for entry in standups if entry.get("blockers")]
        return {
            "entries": len(standups),
            "members": sorted({entry["user_id"] for entry in standups}),
            "blockers": blockers,
        }

    def today_for_actor(self, actor: Actor) -> str:
        return datetime.now(UTC).date().isoformat()

    def resolve_entry_date(self, actor: Actor, value: str | None) -> str:
        return self._normalize_date(value or self.today_for_actor(actor))

    def get_standup(self, actor: Actor, standup_id: int) -> dict:
        standup = self.repo.get_standup(standup_id)
        ensure_can_view_standup(actor, standup["user_id"])
        return standup

    def search_standups(self, actor: Actor, query: str, *, days: int = 30) -> list[dict]:
        needle = self._require_text(query, "Search text is required").lower()
        return [
            standup
            for standup in self.list_recent(actor, days=max(days, 1))
            if any(
                needle in (standup.get(field) or "").lower()
                for field in ("user_id", "date", "previous", "current", "blockers")
            )
        ]

    def _normalize_date(self, value: str | None) -> str:
        clean_value = (value or datetime.now(UTC).date().isoformat()).strip()
        try:
            return date.fromisoformat(clean_value).isoformat()
        except ValueError as exc:
            raise InvalidInputError("Use a valid date in YYYY-MM-DD format.") from exc

    def _require_text(self, value: str, message: str) -> str:
        clean_value = value.strip()
        if not clean_value:
            raise InvalidInputError(message)
        return clean_value

    def _append_activity(self, event_type: str, standup: dict, actor_id: str, metadata: dict) -> None:
        if not self.activity_repo:
            return
        try:
            self.activity_repo.append(
                event_type=event_type,
                entity_type="standup",
                entity_id=standup_code(standup),
                actor_id=actor_id,
                metadata=metadata,
            )
        except Exception:  # pragma: no cover - defensive logging path
            logger.exception(
                "Failed to append standup activity",
                extra={"event_type": event_type, "standup_id": standup["id"]},
            )
