from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cse_hq_bot.db import Database
from cse_hq_bot.errors import PermissionDeniedError
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.repositories.dashboard_publication_repository import (
    DashboardPublicationRepository,
)
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.weekly_dashboard_service import WeeklyDashboardService


def build_service(tmp_path):
    db = Database(str(tmp_path / "cse-hq.db"))
    db.initialize()
    repo = DashboardPublicationRepository(db)
    project_service = ProjectService(ProjectRepository(db))
    return WeeklyDashboardService(repo, project_service), repo


def test_weekly_dashboard_configuration_requires_leadership(tmp_path):
    service, _ = build_service(tmp_path)
    member = Actor("member-1", Role.MEMBER)

    with pytest.raises(PermissionDeniedError):
        service.configure(
            member,
            channel_id="123",
            weekday="monday",
            publish_time="09:00",
        )


def test_weekly_dashboard_due_publish_and_dedupe(tmp_path):
    service, repo = build_service(tmp_path)
    leader = Actor("leader-1", Role.LEADER)
    service.configure(
        leader,
        channel_id="123456",
        weekday="monday",
        publish_time="09:00",
    )

    timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    before = datetime(2026, 9, 21, 8, 59, tzinfo=timezone)
    due = datetime(2026, 9, 21, 9, 0, tzinfo=timezone)

    assert service.prepare_due(before) is None

    payload = service.prepare_due(due)
    assert payload is not None
    assert payload["week_key"] == "2026-W39"
    assert payload["channel_id"] == "123456"
    assert '"name": "CSE-HQ Project"' in payload["snapshot_json"]

    assert service.record_publication(
        week_key=payload["week_key"],
        channel_id=payload["channel_id"],
        message_id="999",
        snapshot_json=payload["snapshot_json"],
    )
    assert not service.record_publication(
        week_key=payload["week_key"],
        channel_id=payload["channel_id"],
        message_id="1000",
        snapshot_json=payload["snapshot_json"],
    )
    assert service.prepare_due(due) is None
    assert repo.get_publication("2026-W39")["message_id"] == "999"


def test_scheduler_catches_up_after_scheduled_day(tmp_path):
    service, _ = build_service(tmp_path)
    leader = Actor("leader-1", Role.CO_LEAD)
    service.configure(
        leader,
        channel_id="123456",
        weekday="monday",
        publish_time="09:00",
    )

    timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    tuesday = datetime(2026, 9, 22, 14, 30, tzinfo=timezone)

    payload = service.prepare_due(tuesday)
    assert payload is not None
    assert payload["week_key"] == "2026-W39"


def test_manual_weekly_dashboard_can_refresh_existing_publication(tmp_path):
    service, repo = build_service(tmp_path)
    leader = Actor("leader-1", Role.LEADER)
    service.configure(
        leader,
        channel_id="123456",
        weekday="monday",
        publish_time="09:00",
    )
    timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    now = datetime(2026, 9, 22, 14, 30, tzinfo=timezone)

    initial = service.prepare_manual(leader, now)
    assert initial["existing_publication"] is None
    assert service.record_publication(
        week_key=initial["week_key"],
        channel_id=initial["channel_id"],
        message_id="999",
        snapshot_json=initial["snapshot_json"],
    )

    refreshed = service.prepare_manual(leader, now)
    assert refreshed["week_key"] == "2026-W39"
    assert refreshed["existing_publication"]["message_id"] == "999"

    service.replace_publication(
        week_key=refreshed["week_key"],
        channel_id=refreshed["channel_id"],
        message_id="1000",
        snapshot_json=refreshed["snapshot_json"],
    )
    publication = repo.get_publication("2026-W39")
    assert publication["message_id"] == "1000"
    assert publication["channel_id"] == "123456"


def test_scheduled_weekly_dashboard_remains_idempotent_after_manual_refresh(tmp_path):
    service, _ = build_service(tmp_path)
    leader = Actor("leader-1", Role.LEADER)
    service.configure(
        leader,
        channel_id="123456",
        weekday="monday",
        publish_time="09:00",
    )
    timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    due = datetime(2026, 9, 21, 9, 0, tzinfo=timezone)

    payload = service.prepare_manual(leader, due)
    assert service.record_publication(
        week_key=payload["week_key"],
        channel_id=payload["channel_id"],
        message_id="999",
        snapshot_json=payload["snapshot_json"],
    )
    assert service.prepare_due(due) is None
