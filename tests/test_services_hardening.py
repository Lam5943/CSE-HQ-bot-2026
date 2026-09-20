from pathlib import Path

import pytest

from cse_hq_bot.db import Database
from cse_hq_bot.errors import InvalidTransitionError, NotFoundError, PermissionDeniedError
from cse_hq_bot.models import Actor, BugStatus, Role, TaskStatus
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.task_service import TaskService


@pytest.fixture
def services(tmp_path: Path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    return {
        "task": TaskService(TaskRepository(db)),
        "bug": BugService(BugRepository(db)),
    }


def test_task_transitions_valid_invalid_unauthorized_and_missing(services):
    leader = Actor("lead", Role.LEADER)
    member_a = Actor("a", Role.MEMBER)
    member_b = Actor("b", Role.MEMBER)

    task_id = services["task"].create_task(member_a, "task", "desc", 3, assignee_id="a")
    with pytest.raises(InvalidTransitionError):
        services["task"].complete_task(member_a, task_id)

    services["task"].start_task(member_a, task_id)
    assert services["task"].get_task(member_a, task_id)["status"] == TaskStatus.IN_PROGRESS.value

    with pytest.raises(PermissionDeniedError):
        services["task"].block_task(member_b, task_id)

    with pytest.raises(NotFoundError):
        services["task"].transition_status(leader, 999, TaskStatus.IN_PROGRESS.value)


def test_task_invalid_transition_rejected(services):
    member = Actor("a", Role.MEMBER)
    task_id = services["task"].create_task(member, "task", "desc", 2, assignee_id="a")
    with pytest.raises(InvalidTransitionError):
        services["task"].complete_task(member, task_id)
    services["task"].start_task(member, task_id)
    services["task"].block_task(member, task_id)
    with pytest.raises(InvalidTransitionError):
        services["task"].complete_task(member, task_id)


def test_bug_transitions_valid_invalid_unauthorized_and_missing(services):
    leader = Actor("lead", Role.LEADER)
    member_a = Actor("a", Role.MEMBER)
    member_b = Actor("b", Role.MEMBER)

    bug_id = services["bug"].report_bug(member_a, "bug", "desc", 3, assignee_id="a")
    services["bug"].transition_status(member_a, bug_id, BugStatus.IN_PROGRESS.value)
    services["bug"].resolve_bug(member_a, bug_id)
    services["bug"].reopen_bug(leader, bug_id)
    assert services["bug"].get_bug(leader, bug_id)["status"] == BugStatus.OPEN.value

    services["bug"].resolve_bug(leader, bug_id)
    with pytest.raises(InvalidTransitionError):
        services["bug"].transition_status(leader, bug_id, BugStatus.IN_PROGRESS.value)

    bug_unauthorized_id = services["bug"].report_bug(member_a, "unauthorized bug", "desc", 2, assignee_id="a")
    with pytest.raises(PermissionDeniedError):
        services["bug"].transition_status(member_b, bug_unauthorized_id, BugStatus.IN_PROGRESS.value)

    with pytest.raises(NotFoundError):
        services["bug"].transition_status(leader, 999, BugStatus.IN_PROGRESS.value)


def test_assignment_permissions_and_missing_targets(services):
    leader = Actor("lead", Role.LEADER)
    member_a = Actor("a", Role.MEMBER)
    member_b = Actor("b", Role.MEMBER)
    member_c = Actor("c", Role.MEMBER)

    task_id = services["task"].create_task(member_a, "task", "desc", 3, assignee_id="a")
    services["task"].assign_task(leader, task_id, "b")
    assert services["task"].get_task(leader, task_id)["assignee_id"] == "b"
    with pytest.raises(PermissionDeniedError):
        services["task"].assign_task(member_c, task_id, "a")
    with pytest.raises(NotFoundError):
        services["task"].assign_task(leader, 999, "a")

    bug_id = services["bug"].report_bug(member_a, "bug", "desc", 3, assignee_id="a")
    services["bug"].assign_bug(leader, bug_id, "b")
    assert services["bug"].get_bug(leader, bug_id)["assignee_id"] == "b"
    with pytest.raises(PermissionDeniedError):
        services["bug"].assign_bug(member_c, bug_id, "a")
    with pytest.raises(NotFoundError):
        services["bug"].assign_bug(leader, 999, "a")


def test_task_create_supports_optional_source_meeting_id(services):
    leader = Actor("lead", Role.LEADER)
    task_id = services["task"].create_task(
        leader,
        "Action item",
        "Created from meeting",
        3,
        source_meeting_id=42,
    )
    task = services["task"].get_task(leader, task_id)
    assert task["source_meeting_id"] == 42

    unrelated_task_id = services["task"].create_task(leader, "Regular task", "No meeting", 2)
    unrelated_task = services["task"].get_task(leader, unrelated_task_id)
    assert unrelated_task["source_meeting_id"] is None


def test_task_filter_combinations(services):
    leader = Actor("lead", Role.LEADER)
    services["task"].create_task(
        leader, "API refactor", "cleanup endpoints", 2, assignee_id="a", deadline="2026-10-01"
    )
    task_2 = services["task"].create_task(
        leader, "Fix bug dashboard", "critical dashboard bug", 5, assignee_id="b", deadline="2026-10-02"
    )
    services["task"].start_task(leader, task_2)

    assert len(services["task"].filter_tasks(leader, status=TaskStatus.TODO.value)) == 1
    assert len(services["task"].filter_tasks(leader, assignee_id="a")) == 1
    assert len(services["task"].filter_tasks(leader, priority=5)) == 1
    assert len(services["task"].filter_tasks(leader, search="dashboard")) == 1
    assert len(
        services["task"].filter_tasks(leader, status=TaskStatus.IN_PROGRESS.value, assignee_id="b")
    ) == 1
    assert len(services["task"].filter_tasks(leader, priority=2, deadline="2026-10-01")) == 1
    assert len(
        services["task"].filter_tasks(
            leader,
            status=TaskStatus.IN_PROGRESS.value,
            search="dashboard",
            assignee_id="b",
        )
    ) == 1


def test_bug_filter_combinations(services):
    leader = Actor("lead", Role.LEADER)
    services["bug"].report_bug(leader, "Crash startup", "null pointer", 5, assignee_id="a")
    bug_2 = services["bug"].report_bug(leader, "UI typo", "button typo in dashboard", 1, assignee_id="b")
    services["bug"].transition_status(leader, bug_2, BugStatus.TRIAGED.value)

    assert len(services["bug"].filter_bugs(leader, status=BugStatus.OPEN.value)) == 1
    assert len(services["bug"].filter_bugs(leader, severity=5)) == 1
    assert len(services["bug"].filter_bugs(leader, assignee_id="b")) == 1
    assert len(services["bug"].filter_bugs(leader, reporter_id="lead")) == 2
    assert len(services["bug"].filter_bugs(leader, search="dashboard")) == 1
    assert len(
        services["bug"].filter_bugs(leader, status=BugStatus.TRIAGED.value, assignee_id="b")
    ) == 1


def test_bug_reopen_only_allows_resolved_to_open(services):
    leader = Actor("lead", Role.LEADER)
    bug_id = services["bug"].report_bug(leader, "bug", "desc", 3, assignee_id="lead")
    with pytest.raises(InvalidTransitionError):
        services["bug"].reopen_bug(leader, bug_id)
    services["bug"].transition_status(leader, bug_id, BugStatus.IN_PROGRESS.value)
    services["bug"].resolve_bug(leader, bug_id)
    services["bug"].reopen_bug(leader, bug_id)
    assert services["bug"].get_bug(leader, bug_id)["status"] == BugStatus.OPEN.value
    services["bug"].transition_status(leader, bug_id, BugStatus.IN_PROGRESS.value)
    services["bug"].resolve_bug(leader, bug_id)
    with pytest.raises(InvalidTransitionError):
        services["bug"].transition_status(leader, bug_id, BugStatus.IN_PROGRESS.value)
