from datetime import UTC, datetime

from cse_hq_bot.ai.action_models import ActionProposal
from cse_hq_bot.models import ProjectDashboard
from cse_hq_bot.ui import (
    _pagination_state,
    build_ai_action_embed,
    build_ai_home_embed,
    build_ai_sessions_embed,
    build_bug_detail_embed,
    build_bugs_embed,
    build_dashboard_embed,
    build_decision_detail_embed,
    build_decisions_embed,
    build_meeting_detail_embed,
    build_meetings_embed,
    build_standup_embed,
    build_task_detail_embed,
    build_tasks_embed,
    split_ai_response,
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
        updated_at=datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC),
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


def test_build_bugs_embed_paginates_and_shows_filter_metadata():
    bugs = [
        {"id": index, "status": "open", "severity": 3, "title": f"Bug {index}", "description": ""}
        for index in range(1, 12)
    ]
    embed = build_bugs_embed(bugs, show_all=True, page=1, filters_label="severity:3")
    assert embed.fields[0].name == "Filters"
    assert embed.fields[0].value == "severity:3"
    assert "Bug 9" in (embed.description or "")
    assert embed.footer.text == "Page 2/2 • Showing 3/11 bugs"


def test_pagination_state_handles_empty_bounds_and_stale_pages():
    assert _pagination_state(0, 0) == (0, 1, True, True)
    assert _pagination_state(12, 0) == (0, 2, True, False)
    assert _pagination_state(12, 1) == (1, 2, False, True)
    assert _pagination_state(12, 50) == (1, 2, False, True)


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


def test_build_meetings_embed_states_and_detail_actions():
    meetings = [
        {
            "id": 1,
            "code": "MEETING-001",
            "title": "Weekly Sync",
            "description": "Discuss progress",
            "agenda": "Status round robin",
            "status": "in_progress",
            "created_by": "lead",
            "scheduled_at": "2026-09-20 09:00",
        }
    ]
    embed = build_meetings_embed(meetings, mode_label="Active")
    detail = build_meeting_detail_embed(
        meetings[0],
        participants=[{"user_id": "u1"}, {"user_id": "u2"}],
        notes=[{"author_id": "u1", "content": "Finished API review"}],
        can_manage=True,
        can_add_note=True,
    )
    assert "MEETING-001" in (embed.description or "")
    assert embed.fields[0].value == "Active"
    assert "Record Decision" in detail.fields[-1].value
    assert "Participants (2)" == detail.fields[3].name


def test_build_decisions_embed_and_detail_states():
    decisions = [
        {
            "id": 1,
            "code": "DEC-001",
            "title": "Keep SQLite",
            "decision": "Use SQLite for v1",
            "context": "Local deployment",
            "rationale": "Simple",
            "alternatives": "Postgres",
            "meeting_id": 1,
            "created_by": "lead",
            "updated_at": "2026-09-20",
        }
    ]
    embed = build_decisions_embed(decisions)
    detail = build_decision_detail_embed(decisions[0], can_edit=False)
    assert "DEC-001" in (embed.description or "")
    assert detail.fields[-1].value == "Refresh, Back"


def test_build_standup_embed_empty_and_populated_states():
    empty_embed = build_standup_embed(None, [], mode_label="History")
    populated = build_standup_embed(
        {"previous": "Yesterday work", "current": "Today work", "blockers": "", "date": "2026-09-20"},
        [{"date": "2026-09-20", "user_id": "u1", "current": "Today work"}],
    )
    assert empty_embed.description == "No standups found."
    assert populated.fields[0].value == "Submitted"
    assert "u1" in (populated.description or "")


def test_build_ai_home_and_sessions_embeds_and_chunking():
    home = build_ai_home_embed()
    sessions = build_ai_sessions_embed([
        {"id": 1, "status": "ACTIVE", "discord_thread_id": "123", "last_active_at": "2026-09-20 12:00:00"}
    ])
    chunks = split_ai_response("Paragraph one.\n\nParagraph two with TASK-001 and details." * 40, limit=120)
    assert home.title == "CSE-HQ AI Assistant"
    assert "Read-only" in home.fields[1].value
    assert "thread `123`" in (sessions.description or "")
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert any("TASK-001" in chunk for chunk in chunks)


def test_build_ai_action_embed_states_no_mutation_before_confirmation():
    proposal = ActionProposal(
        id=1,
        session_id=2,
        actor_id="owner",
        action_type="task_complete",
        target_type="task",
        target_id=14,
        arguments={},
        summary='TASK-014 "Prepare dataset": in_progress → done',
        expected_state={"status": "in_progress"},
        status="PENDING",
        source_message_id=3,
        created_at="2026-09-21T12:00:00+00:00",
        expires_at="2026-09-21T12:10:00+00:00",
    )

    embed = build_ai_action_embed(proposal)

    assert embed.title == "🤖 AI Action Proposal"
    assert "TASK-014" in (embed.description or "")
    assert "No project data has changed" in embed.fields[2].value
