from cse_hq_bot.models import Actor
from cse_hq_bot.permissions import ensure_can_manage_project
from cse_hq_bot.repositories.collab_repository import CollaborationRepository


class CollaborationService:
    def __init__(self, repo: CollaborationRepository):
        self.repo = repo

    def schedule_meeting(self, actor: Actor, title: str, notes: str, meeting_date: str) -> int:
        ensure_can_manage_project(actor)
        return self.repo.create_meeting(title=title, notes=notes, meeting_date=meeting_date, created_by=actor.user_id)

    def record_decision(self, actor: Actor, summary: str, meeting_id: int | None = None) -> int:
        ensure_can_manage_project(actor)
        return self.repo.create_decision(summary=summary, decided_by=actor.user_id, meeting_id=meeting_id)

    def submit_standup(self, actor: Actor, update_text: str, blockers: str = "") -> int:
        return self.repo.create_standup(member_id=actor.user_id, update_text=update_text, blockers=blockers)

    def weekly_standup_summary(self, days: int = 7) -> dict:
        standups = self.repo.list_recent_standups(days=days)
        blockers = [entry["blockers"] for entry in standups if entry["blockers"]]
        return {
            "entries": len(standups),
            "members": sorted({entry["member_id"] for entry in standups}),
            "blockers": blockers,
        }
