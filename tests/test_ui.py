from datetime import datetime

from cse_hq_bot.models import ProjectDashboard
from cse_hq_bot.ui import (
    build_bug_detail_embed,
    build_bugs_embed,
    build_dashboard_embed,
    build_task_detail_embed,
    build_tasks_embed,
)


def test_build_dashboard_embed_contains_management_fields():
    dashboard = ProjectDashboard(
        name="Alpha",
        description="Project summary",
        goal="Ship MVP",
        phase="Execution",
        sprint="Sprint 2",
        deadline="2026-10-10",
        status="On Track",
        updated_at=datetime(2026, 9, 20, 12, 0, 0),
        task_total=10,
        task_open=4,
        task_done=6,
        bug_total=3,
        bug_open=1,
        meetings_total=2,
    )
    embed = build_dashboard_embed(dashboard)
    assert embed.title == "Alpha Dashboard"
    field_values = {field.name: field.value for field in embed.fields}
    assert field_values["Goal"] == "Ship MVP"
    assert field_values["Status"] == "On Track"
    assert field_values["Open Tasks"] == "4"


def test_build_tasks_and_bugs_embed_empty_states():
    task_embed = build_tasks_embed([])
    bug_embed = build_bugs_embed([], show_all=False)
    assert task_embed.description == "No tasks found."
    assert bug_embed.description == "No bugs found."


def test_build_tasks_embed_paginates_and_shows_scope():
    tasks = [
        {"id": index, "status": "todo", "priority": 3, "title": f"Task {index}", "description": ""}
        for index in range(1, 14)
    ]
    embed = build_tasks_embed(tasks, page=1, mode_label="Accessible Tasks", filters_label="status:todo")
    assert embed.fields[0].name == "Scope"
    assert embed.fields[0].value == "Accessible Tasks"
    assert embed.fields[1].value == "status:todo"
    assert "Task 9" in (embed.description or "")
    assert embed.footer.text == "Page 2/2 • Showing 5/13 tasks"


def test_build_detail_embeds_show_action_visibility():
    task = {
        "id": 7,
        "title": "Finish docs",
        "description": "Write release notes",
        "status": "in_progress",
        "priority": 4,
        "assignee_id": "u1",
        "created_by": "u2",
        "deadline": "2026-10-01",
        "created_at": "2026-09-20",
    }
    bug = {
        "id": 5,
        "title": "Crash on startup",
        "description": "Null pointer",
        "status": "open",
        "severity": 5,
        "assignee_id": None,
        "created_by": "u1",
        "created_at": "2026-09-20",
        "resolved_at": None,
    }
    task_embed = build_task_detail_embed(task, can_modify=False)
    bug_embed = build_bug_detail_embed(bug, can_modify=True)
    assert "View only" in task_embed.fields[-1].value
    assert "Set Status" in bug_embed.fields[-1].value
