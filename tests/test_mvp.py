from pathlib import Path

import pytest

from cse_hq_bot.db import Database
from cse_hq_bot.errors import PermissionDeniedError
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.collab_service import CollaborationService
from cse_hq_bot.services.project_service import ProjectService
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

    activity_repo = ActivityRepository(db)
    project_service = ProjectService(project_repo)
    task_service = TaskService(task_repo, activity_repo)
    bug_service = BugService(bug_repo, activity_repo)
    collab_service = CollaborationService(collab_repo, activity_repo)
    report_service = ReportService(project_service, task_service, bug_service, collab_service.standup_service)
    from cse_hq_bot.ai.fake_provider import FakeAIProvider
    from cse_hq_bot.services.activity_service import ActivityService
    from cse_hq_bot.services.ai_service import AIService
    from cse_hq_bot.services.project_context_service import ProjectContextService
    from cse_hq_bot.services.prompt_builder import PromptBuilder
    from cse_hq_bot.services.qa_service import QAService
    from cse_hq_bot.services.retrieval_planner import RetrievalPlanner

    context_service = ProjectContextService(
        project_service,
        task_service,
        bug_service,
        collab_service.meeting_service,
        collab_service.decision_service,
        collab_service.standup_service,
        ActivityService(activity_repo),
    )
    ai_service = AIService(FakeAIProvider(), context_service, RetrievalPlanner(), PromptBuilder(), max_context_items=5, request_timeout=5)
    qa_service = QAService(ai_service)

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
    services["project"].update_management(
        leader,
        goal="Ship MVP",
        phase="Development",
        sprint="Sprint 1",
        deadline="2026-10-01",
        status="On Track",
    )
    dashboard = services["project"].get_dashboard()
    assert dashboard.name == "Alpha"
    assert dashboard.goal == "Ship MVP"
    assert dashboard.phase == "Development"
    assert dashboard.sprint == "Sprint 1"
    assert dashboard.deadline == "2026-10-01"
    assert dashboard.status == "On Track"

    with pytest.raises(PermissionDeniedError):
        services["project"].update_settings(member, "Nope", "Nope")
    services["project"].ensure_can_manage(leader)
    with pytest.raises(PermissionDeniedError):
        services["project"].ensure_can_manage(member)
    with pytest.raises(PermissionDeniedError):
        services["project"].update_management(member, status="Blocked")


def test_task_and_bug_workflow_with_permissions(services):
    leader = Actor("lead", Role.LEADER)
    member_a = Actor("a", Role.MEMBER)
    member_b = Actor("b", Role.MEMBER)

    task_id = services["task"].create_task(member_a, "task", "desc", 2, assignee_id="a")
    services["task"].update_task(member_a, task_id, status="in_progress")

    with pytest.raises(PermissionDeniedError):
        services["task"].update_task(member_b, task_id, status="done")

    services["task"].complete_task(leader, task_id)
    dashboard = services["project"].get_dashboard()
    assert dashboard.task_total == 1
    assert dashboard.task_done == 1
    assert dashboard.task_open == 0

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
    assert "System Instruction" in answer
    assert "read-only" in answer.lower()
