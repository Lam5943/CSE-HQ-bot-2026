from datetime import UTC, date, datetime

from cse_hq_bot.errors import InvalidInputError
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.standup_service import StandupService


class CollaborationService:
    def __init__(self, repo: CollaborationRepository, activity_repo: ActivityRepository | None = None):
        self.meeting_service = MeetingService(repo, activity_repo)
        self.decision_service = DecisionService(repo, activity_repo)
        self.standup_service = StandupService(repo, activity_repo)

    def schedule_meeting(self, actor, title: str, notes: str, meeting_date: str) -> int:
        scheduled_at = meeting_date
        stripped = meeting_date.strip()
        if stripped:
            try:
                scheduled_at = f"{date.fromisoformat(stripped).isoformat()} 00:00"
            except ValueError:
                scheduled_at = meeting_date
        return self.meeting_service.create_meeting(
            actor,
            title=title,
            description="",
            agenda=notes,
            scheduled_at=scheduled_at,
        )

    def record_decision(self, actor, summary: str, meeting_id: int | None = None) -> int:
        clean_summary = summary.strip()
        if not clean_summary:
            raise InvalidInputError("Decision text is required")
        return self.decision_service.create_decision(
            actor,
            title=clean_summary[:120],
            decision=clean_summary,
            context="",
            rationale="",
            alternatives="",
            meeting_id=meeting_id,
        )

    def submit_standup(self, actor, update_text: str, blockers: str = "") -> int:
        return self.standup_service.submit_standup(
            actor,
            previous="",
            current=update_text,
            blockers=blockers,
            entry_date=datetime.now(UTC).date().isoformat(),
            allow_empty_previous=True,
        )

    def weekly_standup_summary(self, days: int = 7) -> dict:
        return self.standup_service.weekly_standup_summary(days=days)
