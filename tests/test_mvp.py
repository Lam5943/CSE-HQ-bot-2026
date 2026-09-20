from pathlib import Path

import pytest

from cse_hq_bot.ai.fake_provider import FakeAIProvider
from cse_hq_bot.db import Database
from cse_hq_bot.errors import PermissionDeniedError
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.collab_service import CollaborationService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.qa_service import QAService
from cse_hq_bot.services.report_service import ReportService
from cse_hq_bot.services.task_service import TaskService


@pytest.fixture
def services(tmp_path: Path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.initialize()

    project_repo = ProjectRepository(db)
    task_repo = TaskRepository(db)
    bug_repo = BugRepository(db)
    collab_repo = CollaborationRepository(db)

    project_service = ProjectService(project_repo)
    task_service = TaskService(task_repo)
    bug_service = BugService(bug_repo)
    collab_service = CollaborationService(collab_repo)
    report_service = ReportService(project_service, task_service, bug_service, collab_service)
    qa_service = QAService(FakeAIProvider(), project_repo, task_repo, bug_repo)

    return {
        "project": project_service,
        "task": task_service,
        "bug": bug_service,
        "collab": collab_service,
        "report": report_service,
        "qa": qa_service,
    }


def test_dashboard_and_project_permissions(services):
    leader = Actor("u1", Role.LEADER)
    member = Actor("u2", Role.MEMBER)

    services["project"].update_settings(leader, "Alpha", "Desc")
    dashboard = services["project"].get_dashboard()
    assert dashboard.name == "Alpha"

    with pytest.raises(PermissionDeniedError):
        services["project"].update_settings(member, "Nope", "Nope")


def test_task_and_bug_workflow_with_permissions(services):
    leader = Actor("lead", Role.LEADER)
    member_a = Actor("a", Role.MEMBER)
    member_b = Actor("b", Role.MEMBER)

    task_id = services["task"].create_task(member_a, "task", "desc", 2, assignee_id="a")
    services["task"].update_task(member_a, task_id, status="in_progress")

    with pytest.raises(PermissionDeniedError):
        services["task"].update_task(member_b, task_id, status="done")

    services["task"].complete_task(leader, task_id)

    bug_id = services["bug"].report_bug(member_a, "bug", "desc", 3, assignee_id="a")
    services["bug"].update_bug(member_a, bug_id, status="triaged")

    with pytest.raises(PermissionDeniedError):
        services["bug"].update_bug(member_b, bug_id, status="resolved")

    services["bug"].resolve_bug(leader, bug_id)
    assert len(services["bug"].list_bugs()) == 1


def test_meetings_standups_reporting_and_qa(services):
    leader = Actor("lead", Role.LEADER)
    member = Actor("m1", Role.MEMBER)

    services["collab"].schedule_meeting(leader, "Weekly", "notes", "2026-09-20")
    services["collab"].record_decision(leader, "Use sqlite for MVP")
    services["collab"].submit_standup(member, "Worked on API", "Need review")

    report = services["report"].weekly_progress_report()
    assert "Weekly Report" in report
    assert "Standups: 1 updates" in report

    answer = services["qa"].ask("What is the project status?")
    assert "Prompt Context" in answer
    assert "You are a project assistant" in answer
