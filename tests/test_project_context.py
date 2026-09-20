from pathlib import Path

import pytest

from cse_hq_bot.db import Database
from cse_hq_bot.errors import InvalidInputError, PermissionDeniedError
from cse_hq_bot.models import Actor, BugStatus, Role, TaskStatus
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.activity_service import ActivityService
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.project_context_service import ProjectContextService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService


@pytest.fixture
def services(tmp_path: Path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    activity_repo = ActivityRepository(db)
    task_service = TaskService(TaskRepository(db), activity_repo)
    bug_service = BugService(BugRepository(db), activity_repo)
    collab_repo = CollaborationRepository(db)
    meeting_service = MeetingService(collab_repo, activity_repo)
    decision_service = DecisionService(collab_repo, activity_repo)
    standup_service = StandupService(collab_repo, activity_repo)
    project_service = ProjectService(ProjectRepository(db))
    activity_service = ActivityService(activity_repo)
    return {
        "task": task_service,
        "bug": bug_service,
        "meeting": meeting_service,
        "decision": decision_service,
        "standup": standup_service,
        "project": project_service,
        "activity": activity_service,
        "context": ProjectContextService(
            project_service,
            task_service,
            bug_service,
            meeting_service,
            decision_service,
            standup_service,
            activity_service,
        ),
    }


def test_activity_records_successful_mutations_and_not_failed_ones(services):
    leader = Actor("lead", Role.LEADER)
    member = Actor("member", Role.MEMBER)
    outsider = Actor("outsider", Role.MEMBER)

    task_id = services["task"].create_task(member, "Ship API", "Initial implementation", 3, assignee_id="member")
    services["task"].start_task(member, task_id)
    services["task"].assign_task(leader, task_id, "lead")
    with pytest.raises(PermissionDeniedError):
        services["task"].complete_task(outsider, task_id)

    activities = services["activity"].list_entity_activity("task", "TASK-001", limit=10)
    assert [entry["event_type"] for entry in activities] == [
        "TASK_ASSIGNED",
        "TASK_STATUS_CHANGED",
        "TASK_CREATED",
    ]
    status_activity = next(entry for entry in activities if entry["event_type"] == "TASK_STATUS_CHANGED")
    assert status_activity["metadata"]["from"] == TaskStatus.TODO.value
    assert status_activity["metadata"]["to"] == TaskStatus.IN_PROGRESS.value
    assert all(entry["metadata"]["row_id"] == task_id for entry in activities)


def test_bug_activity_and_decision_edit_metadata(services):
    leader = Actor("lead", Role.LEADER)

    bug_id = services["bug"].report_bug(leader, "Crash", "Null pointer", 5, assignee_id="lead")
    services["bug"].transition_status(leader, bug_id, BugStatus.TRIAGED.value)
    services["bug"].assign_bug(leader, bug_id, "other")

    meeting_id = services["meeting"].create_meeting(leader, "Review", "", "", "2026-09-21 09:00")
    decision_id = services["decision"].create_decision(
        leader,
        title="Keep SQLite",
        decision="Keep SQLite storage",
        context="Small deployment",
        rationale="Easy backups",
        alternatives="Postgres",
        meeting_id=meeting_id,
    )
    services["decision"].edit_decision(leader, decision_id, rationale="Easy backups and portability", title="Keep SQLite v1")

    bug_activities = services["activity"].list_entity_activity("bug", "BUG-001", limit=10)
    assert [entry["event_type"] for entry in bug_activities][:3] == [
        "BUG_ASSIGNED",
        "BUG_STATUS_CHANGED",
        "BUG_REPORTED",
    ]
    decision_activity = services["activity"].list_entity_activity("decision", "DEC-001", limit=10)[0]
    assert decision_activity["event_type"] == "DECISION_EDITED"
    assert decision_activity["metadata"]["changed_fields"] == ["title", "rationale"]


def test_meeting_and_standup_activity_filters_bounds_and_ranges(services):
    leader = Actor("lead", Role.LEADER)
    member = Actor("member", Role.MEMBER)

    meeting_id = services["meeting"].create_meeting(leader, "Sprint Planning", "", "", "2026-09-21 09:00")
    services["meeting"].add_participant(leader, meeting_id, member.user_id)
    services["meeting"].start_meeting(leader, meeting_id)
    services["meeting"].add_note(member, meeting_id, "Reviewed top priorities.")

    first_standup_id = services["standup"].submit_standup(
        member,
        previous="Finished API",
        current="Write tests",
        blockers="Need review",
        entry_date="2026-09-20",
    )
    second_standup_id = services["standup"].submit_standup(
        member,
        previous="Finished API and docs",
        current="Write tests",
        blockers="",
        entry_date="2026-09-20",
    )

    meeting_activities = services["activity"].list_entity_activity("meeting", "MEETING-001", limit=10)
    assert [entry["event_type"] for entry in meeting_activities] == [
        "MEETING_NOTE_ADDED",
        "MEETING_STATUS_CHANGED",
        "MEETING_PARTICIPANT_ADDED",
        "MEETING_CREATED",
    ]

    standup_activities = services["activity"].list_entity_activity("standup", "STANDUP-001", limit=10)
    assert [entry["event_type"] for entry in standup_activities] == [
        "STANDUP_UPDATED",
        "STANDUP_SUBMITTED",
    ]
    assert standup_activities[0]["metadata"]["date"] == "2026-09-20"
    assert standup_activities[1]["metadata"]["has_blockers"] is True
    assert first_standup_id == second_standup_id

    recent_two = services["activity"].list_recent_activity(limit=2)
    assert len(recent_two) == 2
    between = services["activity"].list_activity_between("2000-01-01 00:00:00", "2999-12-31 23:59:59", limit=20)
    assert len(between) >= len(meeting_activities) + len(standup_activities)
    actor_activity = services["activity"].list_actor_activity("lead", limit=20)
    assert all(entry["actor_id"] == "lead" for entry in actor_activity)


def test_project_context_overview_current_work_blockers_and_meeting_context(services):
    leader = Actor("lead", Role.LEADER)
    member = Actor("member", Role.MEMBER)

    meeting_id = services["meeting"].create_meeting(leader, "Weekly Sync", "Discuss progress", "Agenda", "2026-09-21 10:00")
    services["meeting"].add_participant(leader, meeting_id, member.user_id)
    services["meeting"].start_meeting(leader, meeting_id)
    services["meeting"].add_note(member, meeting_id, "Blocked by deploy access.")
    decision_id = services["decision"].create_decision(
        leader,
        title="Use staging",
        decision="Use staging for QA",
        context="Testing",
        rationale="Safer rollouts",
        alternatives="Direct prod",
        meeting_id=meeting_id,
    )
    active_task_id = services["task"].create_task(member, "Implement API", "Core work", 3, assignee_id="member", source_meeting_id=meeting_id)
    blocked_task_id = services["task"].create_task(member, "Deploy app", "Wait for access", 4, assignee_id="member")
    services["task"].start_task(member, active_task_id)
    services["task"].start_task(member, blocked_task_id)
    services["task"].block_task(member, blocked_task_id)
    bug_id = services["bug"].report_bug(member, "Deploy bug", "staging crash", 5, assignee_id="member")
    services["bug"].transition_status(member, bug_id, BugStatus.TRIAGED.value)
    services["standup"].submit_standup(
        member,
        previous="Built API",
        current="Fix deploy",
        blockers="Waiting for infra",
        entry_date=services["standup"].today_for_actor(member),
    )

    overview = services["context"].get_project_overview(member)
    assert overview["project"]["name"] == "CSE-HQ Project"
    assert isinstance(overview["task_statistics"], dict)
    assert overview["active_meetings"][0]["id"] == meeting_id
    assert overview["recent_decisions"][0]["id"] == decision_id

    current_work = services["context"].get_current_work(leader, user_id="member")
    assert [task["id"] for task in current_work["active_tasks"]] == [active_task_id]
    assert [task["id"] for task in current_work["blocked_tasks"]] == [blocked_task_id]
    assert current_work["current_standup"]["user_id"] == "member"
    assert current_work["open_bugs"][0]["id"] == bug_id

    blockers = services["context"].get_blockers(leader)
    assert blockers["blocked_tasks"][0]["id"] == blocked_task_id
    assert blockers["high_severity_bugs"][0]["id"] == bug_id
    assert blockers["standups_with_blockers"][0]["user_id"] == "member"

    meeting_context = services["context"].get_meeting_context(member, meeting_id)
    assert meeting_context["meeting"]["id"] == meeting_id
    assert meeting_context["participants"][0]["user_id"] == "member"
    assert meeting_context["notes"][0]["content"] == "Blocked by deploy access."
    assert meeting_context["decisions"][0]["id"] == decision_id
    assert meeting_context["tasks"][0]["id"] == active_task_id


def test_project_context_permissions_recent_activity_search_and_empty_state(services):
    leader = Actor("lead", Role.LEADER)
    owner = Actor("owner", Role.MEMBER)
    outsider = Actor("outsider", Role.MEMBER)

    task_id = services["task"].create_task(owner, "Private task", "Confidential plan", 3, assignee_id="owner")
    services["task"].start_task(owner, task_id)
    services["bug"].report_bug(owner, "Owner bug", "private issue", 2, assignee_id="owner")
    services["decision"].create_decision(
        leader,
        title="Public decision",
        decision="Keep docs simple",
        context="Docs",
        rationale="Lower maintenance",
        alternatives="Long docs",
    )
    services["meeting"].create_meeting(leader, "Docs sync", "Discuss docs", "Docs agenda", "2026-09-23 09:00")
    services["standup"].submit_standup(
        owner,
        previous="Docs",
        current="More docs",
        blockers="Need review",
        entry_date="2026-09-19",
    )

    with pytest.raises(PermissionDeniedError):
        services["task"].get_task(outsider, task_id)
    with pytest.raises(PermissionDeniedError):
        services["context"].get_current_work(outsider, user_id="owner")

    outsider_recent = services["context"].get_recent_activity(outsider, limit=10)
    assert all(entry["entity_type"] not in {"task", "standup"} for entry in outsider_recent)
    owner_recent = services["context"].get_recent_activity(owner, limit=10)
    assert any(entry["entity_type"] == "task" for entry in owner_recent)

    ranged = services["context"].get_activity_between(
        owner,
        "2000-01-01 00:00:00",
        "2999-12-31 23:59:59",
        limit=5,
    )
    assert len(ranged) <= 5

    search_results = services["context"].search_project_memory(owner, "docs", limit=10)
    assert search_results
    assert all(isinstance(result, dict) for result in search_results)
    assert {"source_type", "source_id", "title", "snippet", "timestamp", "relevance_hint"} <= set(search_results[0])
    with pytest.raises(InvalidInputError):
        services["context"].search_project_memory(owner, "docs", domains=["unknown"])

    empty = services["context"].get_current_work(outsider)
    assert empty["active_tasks"] == []
    assert empty["blocked_tasks"] == []
    assert empty["current_standup"] is None
