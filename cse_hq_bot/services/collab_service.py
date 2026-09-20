from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.standup_service import StandupService


class CollaborationService:
    def __init__(self, repo: CollaborationRepository):
        self.meeting_service = MeetingService(repo)
        self.decision_service = DecisionService(repo)
        self.standup_service = StandupService(repo)

    def schedule_meeting(self, actor, title: str, notes: str, meeting_date: str) -> int:
        return self.meeting_service.create_meeting(
            actor,
            title=title,
            description="",
            agenda=notes,
            scheduled_at=meeting_date,
        )

    def record_decision(self, actor, summary: str, meeting_id: int | None = None) -> int:
        return self.decision_service.create_decision(
            actor,
            title=summary[:120],
            decision=summary,
            context="",
            rationale="",
            alternatives="",
            meeting_id=meeting_id,
        )

    def submit_standup(self, actor, update_text: str, blockers: str = "") -> int:
        return self.standup_service.submit_standup(
            actor,
            previous=update_text,
            current=update_text,
            blockers=blockers,
        )

    def weekly_standup_summary(self, days: int = 7) -> dict:
        return self.standup_service.weekly_standup_summary(days=days)
