from datetime import datetime

from cse_hq_bot.models import ProjectDashboard
from cse_hq_bot.ui import build_bugs_embed, build_dashboard_embed, build_tasks_embed


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
    assert task_embed.description == "No tasks yet."
    assert bug_embed.description == "No bugs found."
