from datetime import date

from cse_hq_bot.errors import InvalidInputError
from cse_hq_bot.models import Actor
from cse_hq_bot.repositories.collab_repository import CollaborationRepository


class StandupService:
    def __init__(self, repo: CollaborationRepository):
        self.repo = repo

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
        return self.repo.create_standup(
            user_id=actor.user_id,
            entry_date=self._normalize_date(entry_date),
            previous=(
                previous.strip()
                if allow_empty_previous
                else self._require_text(previous, "Previous update is required")
            ),
            current=self._require_text(current, "Current update is required"),
            blockers=blockers.strip(),
        )

    def get_today(self, actor: Actor, entry_date: str | None = None) -> dict | None:
        return self.repo.get_standup_for_user_date(actor.user_id, self.resolve_entry_date(actor, entry_date))

    def list_for_date(self, actor: Actor, entry_date: str) -> list[dict]:
        target_date = self.resolve_entry_date(actor, entry_date)
        return [standup for standup in self.repo.list_standups() if standup.get("date") == target_date]

    def list_recent(self, actor: Actor, days: int = 7) -> list[dict]:
        return self.repo.list_recent_standups(days=max(days, 1))

    def weekly_standup_summary(self, days: int = 7) -> dict:
        standups = self.repo.list_recent_standups(days=max(days, 1))
        blockers = [entry["blockers"] for entry in standups if entry.get("blockers")]
        return {
            "entries": len(standups),
            "members": sorted({entry["user_id"] for entry in standups}),
            "blockers": blockers,
        }

    def today_for_actor(self, actor: Actor) -> str:
        return date.today().isoformat()

    def resolve_entry_date(self, actor: Actor, value: str | None) -> str:
        return self._normalize_date(value or self.today_for_actor(actor))

    def _normalize_date(self, value: str | None) -> str:
        clean_value = (value or date.today().isoformat()).strip()
        try:
            return date.fromisoformat(clean_value).isoformat()
        except ValueError as exc:
            raise InvalidInputError("Use a valid date in YYYY-MM-DD format.") from exc

    def _require_text(self, value: str, message: str) -> str:
        clean_value = value.strip()
        if not clean_value:
            raise InvalidInputError(message)
        return clean_value
