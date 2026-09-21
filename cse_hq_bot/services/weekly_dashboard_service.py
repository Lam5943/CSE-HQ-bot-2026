import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from cse_hq_bot.errors import InvalidInputError
from cse_hq_bot.models import Actor
from cse_hq_bot.permissions import ensure_can_manage_project
from cse_hq_bot.repositories.dashboard_publication_repository import (
    DashboardPublicationRepository,
)
from cse_hq_bot.services.project_service import ProjectService

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"


class WeeklyDashboardService:
    def __init__(
        self,
        repo: DashboardPublicationRepository,
        project_service: ProjectService,
    ):
        self.repo = repo
        self.project_service = project_service

    def configure(
        self,
        actor: Actor,
        *,
        channel_id: str,
        weekday: str = "monday",
        publish_time: str = "09:00",
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict:
        ensure_can_manage_project(actor)
        clean_channel_id = str(channel_id).strip()
        if not clean_channel_id.isdigit() or int(clean_channel_id) <= 0:
            raise InvalidInputError("Dashboard channel ID must be a positive number")

        clean_weekday = weekday.strip().lower()
        if clean_weekday not in WEEKDAYS:
            raise InvalidInputError(
                "Weekday must be Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, or Sunday"
            )
        clean_time = publish_time.strip()
        try:
            hour_text, minute_text = clean_time.split(":", 1)
            if (
                len(clean_time) != 5
                or len(hour_text) != 2
                or len(minute_text) != 2
                or not hour_text.isdigit()
                or not minute_text.isdigit()
            ):
                raise ValueError
            hour = int(hour_text)
            minute = int(minute_text)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
        except ValueError as exc:
            raise InvalidInputError(
                "Publish time must use 24-hour HH:MM format"
            ) from exc
        try:
            ZoneInfo(timezone)
        except Exception as exc:
            raise InvalidInputError("Dashboard timezone is invalid") from exc

        normalized_time = f"{hour:02d}:{minute:02d}"
        self.repo.set_settings(
            channel_id=clean_channel_id,
            weekday=WEEKDAYS[clean_weekday],
            publish_time=normalized_time,
            timezone=timezone,
            configured_by=actor.user_id,
        )
        settings = self.repo.get_settings()
        if settings is None:
            raise InvalidInputError("Unable to persist weekly dashboard configuration")
        return settings

    def get_settings(self) -> dict | None:
        return self.repo.get_settings()

    def prepare_manual(self, actor: Actor, now: datetime | None = None) -> dict:
        ensure_can_manage_project(actor)
        settings = self._require_settings()
        local_now = self._local_now(settings, now)
        return self._prepare(settings, local_now, allow_existing=True)

    def prepare_due(self, now: datetime | None = None) -> dict | None:
        settings = self.repo.get_settings()
        if settings is None:
            return None
        local_now = self._local_now(settings, now)
        if not self._is_schedule_due(settings, local_now):
            return None
        week_key = self.week_key(local_now)
        if self.repo.get_publication(week_key) is not None:
            return None
        return self._prepare(settings, local_now)

    def record_publication(
        self,
        *,
        week_key: str,
        channel_id: str,
        message_id: str,
        snapshot_json: str,
    ) -> bool:
        return self.repo.create_publication(
            week_key=week_key,
            channel_id=channel_id,
            message_id=message_id,
            snapshot_json=snapshot_json,
        )

    def replace_publication(
        self,
        *,
        week_key: str,
        channel_id: str,
        message_id: str,
        snapshot_json: str,
    ) -> None:
        self.repo.upsert_publication(
            week_key=week_key,
            channel_id=channel_id,
            message_id=message_id,
            snapshot_json=snapshot_json,
        )

    def get_publication(self, week_key: str) -> dict | None:
        return self.repo.get_publication(week_key)

    @staticmethod
    def week_key(now: datetime) -> str:
        iso_year, iso_week, _ = now.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"

    def _prepare(
        self,
        settings: dict,
        local_now: datetime,
        *,
        allow_existing: bool = False,
    ) -> dict:
        week_key = self.week_key(local_now)
        existing = self.repo.get_publication(week_key)
        if existing is not None and not allow_existing:
            raise InvalidInputError(
                f"Weekly dashboard {week_key} has already been published"
            )
        dashboard = self.project_service.get_dashboard()
        snapshot = asdict(dashboard)
        snapshot["updated_at"] = dashboard.updated_at.isoformat()
        return {
            "week_key": week_key,
            "channel_id": str(settings["channel_id"]),
            "dashboard": dashboard,
            "snapshot_json": json.dumps(snapshot, sort_keys=True),
            "existing_publication": existing,
        }

    @staticmethod
    def _is_schedule_due(settings: dict, local_now: datetime) -> bool:
        scheduled_weekday = int(settings["weekday"])
        if local_now.weekday() < scheduled_weekday:
            return False
        if local_now.weekday() > scheduled_weekday:
            return True
        scheduled_hour, scheduled_minute = (
            int(part) for part in str(settings["publish_time"]).split(":", 1)
        )
        return (local_now.hour, local_now.minute) >= (
            scheduled_hour,
            scheduled_minute,
        )

    @staticmethod
    def _local_now(settings: dict, now: datetime | None) -> datetime:
        timezone = ZoneInfo(str(settings["timezone"]))
        if now is None:
            return datetime.now(timezone)
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone)
        return now.astimezone(timezone)

    def _require_settings(self) -> dict:
        settings = self.repo.get_settings()
        if settings is None:
            raise InvalidInputError(
                "Weekly dashboard is not configured. Run /setup dashboard first."
            )
        return settings
