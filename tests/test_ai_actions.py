import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from cse_hq_bot.ai.action_models import (
    ActionIntentKind,
    KnownMember,
)
from cse_hq_bot.ai.base import AIProviderResponse
from cse_hq_bot.ai.provider_router import AIProviderRouter
from cse_hq_bot.config import load_config
from cse_hq_bot.db import Database
from cse_hq_bot.errors import (
    AIActionAlreadyHandledError,
    AIActionConflictError,
    AIActionExpiredError,
    AIActionOwnershipError,
    AIActionUnsupportedError,
    AIActionValidationError,
    AITimeoutError,
    PermissionDeniedError,
)
from cse_hq_bot.factory import ServiceContainer
from cse_hq_bot.models import Actor, BugStatus, Role, TaskStatus
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.ai_action_repository import AIActionProposalRepository
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.ai_action_interpreter import (
    ActionIntentDetector,
    AIActionInterpreter,
)
from cse_hq_bot.services.ai_action_registry import AIActionRegistry
from cse_hq_bot.services.ai_action_service import AIActionService
from cse_hq_bot.services.ai_service import GroundedAnswer
from cse_hq_bot.services.ai_session_service import AISessionService
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.task_service import TaskService


class StaticProvider:
    def __init__(self, text: str = "{}", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return AIProviderResponse(text=self.text)


@pytest.fixture
def action_stack(tmp_path):
    db = Database(str(tmp_path / "actions.db"))
    db.initialize()
    activity_repo = ActivityRepository(db)
    task_service = TaskService(TaskRepository(db), activity_repo)
    bug_service = BugService(BugRepository(db), activity_repo)
    session_repo = AISessionRepository(db)
    action_repo = AIActionProposalRepository(db)
    registry = AIActionRegistry(task_service, bug_service)
    clock_value = [datetime(2026, 9, 21, 12, 0, tzinfo=UTC)]
    action_service = AIActionService(
        action_repo,
        session_repo,
        registry,
        expiration_seconds=60,
        clock=lambda: clock_value[0],
    )
    actor = Actor("owner", Role.MEMBER)
    session_id = session_repo.create_session(actor.user_id, "thread-1")
    return {
        "db": db,
        "activity_repo": activity_repo,
        "task": task_service,
        "bug": bug_service,
        "session_repo": session_repo,
        "action_repo": action_repo,
        "registry": registry,
        "action_service": action_service,
        "clock": clock_value,
        "actor": actor,
        "session_id": session_id,
    }


def _create_proposal(stack, draft, actor=None):
    return stack["action_service"].create_proposal(
        actor=actor or stack["actor"],
        session_id=stack["session_id"],
        source_message_id=None,
        draft=draft,
    )


@pytest.mark.parametrize(
    ("question", "kind", "action_type"),
    [
        ("Complete TASK-014", ActionIntentKind.SUPPORTED, "task_complete"),
        ("Can you complete TASK-014?", ActionIntentKind.SUPPORTED, "task_complete"),
        ("Assign BUG-004 to Alice", ActionIntentKind.SUPPORTED, "bug_assign"),
        ("Resolve BUG-004", ActionIntentKind.SUPPORTED, "bug_resolve"),
        ("How do I complete TASK-014?", ActionIntentKind.NONE, None),
        ("Is TASK-014 complete?", ActionIntentKind.NONE, None),
        ("Can TASK-014 be reopened?", ActionIntentKind.NONE, None),
        ("Merge GH-PR-9", ActionIntentKind.UNSUPPORTED, None),
        ("Create a meeting tomorrow", ActionIntentKind.UNSUPPORTED, None),
        (
            "Complete TASK-014 and TASK-015",
            ActionIntentKind.MULTI_ACTION,
            None,
        ),
    ],
)
def test_action_intent_detection(question, kind, action_type):
    intent = ActionIntentDetector().detect(question)

    assert intent.kind == kind
    assert intent.action_type == action_type


def test_registry_rejects_unknown_actions_and_arguments(action_stack):
    registry = action_stack["registry"]
    actor = action_stack["actor"]

    with pytest.raises(AIActionUnsupportedError):
        registry.prepare(
            actor=actor,
            action_type="drop_database",
            target_id=None,
            arguments={},
        )
    with pytest.raises(AIActionValidationError, match="Unsupported action arguments"):
        registry.prepare(
            actor=actor,
            action_type="task_create",
            target_id=None,
            arguments={"title": "Safe", "command": "rm"},
        )
    with pytest.raises(AIActionValidationError, match="required"):
        registry.prepare(
            actor=actor,
            action_type="task_create",
            target_id=None,
            arguments={},
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"title": ""},
        {"title": "Bad priority", "priority": 0},
        {"title": "Bad priority", "priority": 6},
        {"title": "Bad priority", "priority": "high"},
        {"title": "Too much detail", "description": "x" * 2001},
    ],
)
def test_task_create_rejects_empty_and_invalid_fields(action_stack, arguments):
    with pytest.raises(AIActionValidationError):
        action_stack["registry"].prepare(
            actor=action_stack["actor"],
            action_type="task_create",
            target_id=None,
            arguments=arguments,
        )


def test_structured_output_validation_is_strict(action_stack):
    interpreter = AIActionInterpreter(
        StaticProvider(),
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )

    with pytest.raises(AIActionValidationError):
        interpreter.parse_structured_output("not json", expected_action_type="task_complete")
    with pytest.raises(AIActionValidationError, match="unknown fields"):
        interpreter.parse_structured_output(
            '{"kind":"action_proposal","action_type":"task_complete","arguments":{},"function":"drop_database"}',
            expected_action_type="task_complete",
        )
    with pytest.raises(AIActionValidationError, match="changed"):
        interpreter.parse_structured_output(
            '{"kind":"action_proposal","action_type":"bug_resolve","arguments":{}}',
            expected_action_type="task_complete",
        )
    with pytest.raises(AIActionValidationError, match="unknown fields"):
        interpreter.parse_structured_output(
            '{"kind":"action_proposal","action_type":"task_complete",'
            '"actions":[{"target_id":"TASK-001"},{"target_id":"TASK-002"}]}',
            expected_action_type="task_complete",
        )


def test_exact_target_and_known_member_resolution(action_stack):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "Dataset cleanup", "", 3, assignee_id=actor.user_id
    )
    interpreter = AIActionInterpreter(
        StaticProvider(),
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )

    draft = asyncio.run(
        interpreter.interpret(
            actor=actor,
            question=f"Assign TASK-{task_id:03d} to Alice",
            action_type="task_assign",
            history_messages=[],
            known_members=[KnownMember("alice-id", "Alice", "alice")],
        )
    )

    assert draft.target_id == task_id
    assert draft.arguments == {"assignee_id": "alice-id"}


def test_title_resolution_is_unique_or_requests_disambiguation(action_stack):
    actor = action_stack["actor"]
    action_stack["task"].create_task(
        actor, "API authentication", "", 3, assignee_id=actor.user_id
    )
    interpreter = AIActionInterpreter(
        StaticProvider(),
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )
    draft = asyncio.run(
        interpreter.interpret(
            actor=actor,
            question="Start the API authentication task",
            action_type="task_start",
            history_messages=[],
            known_members=[],
        )
    )
    assert draft.target_id == 1

    action_stack["task"].create_task(
        actor, "API documentation", "", 3, assignee_id=actor.user_id
    )
    with pytest.raises(AIActionValidationError, match="Which record"):
        asyncio.run(
            interpreter.interpret(
                actor=actor,
                question="Start the API task",
                action_type="task_start",
                history_messages=[],
                known_members=[],
            )
        )


def test_unknown_and_ambiguous_members_are_rejected(action_stack):
    actor = action_stack["actor"]
    action_stack["task"].create_task(actor, "API", "", 3, assignee_id=actor.user_id)
    interpreter = AIActionInterpreter(
        StaticProvider(),
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )

    with pytest.raises(AIActionValidationError, match="not a known"):
        asyncio.run(
            interpreter.interpret(
                actor=actor,
                question="Assign TASK-001 to Unknown",
                action_type="task_assign",
                history_messages=[],
                known_members=[],
            )
        )
    with pytest.raises(AIActionValidationError, match="ambiguous"):
        asyncio.run(
            interpreter.interpret(
                actor=actor,
                question="Assign TASK-001 to Alice",
                action_type="task_assign",
                history_messages=[],
                known_members=[
                    KnownMember("a1", "Alice", "alice-one"),
                    KnownMember("a2", "Alice", "alice-two"),
                ],
            )
        )


def test_inaccessible_target_is_not_exposed(action_stack):
    owner = action_stack["actor"]
    outsider = Actor("outsider", Role.MEMBER)
    action_stack["task"].create_task(owner, "Secret", "", 3, assignee_id=owner.user_id)
    interpreter = AIActionInterpreter(
        StaticProvider(),
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )

    with pytest.raises(AIActionValidationError, match="unavailable or inaccessible"):
        asyncio.run(
            interpreter.interpret(
                actor=outsider,
                question="Complete TASK-001",
                action_type="task_complete",
                history_messages=[],
                known_members=[],
            )
        )


def test_task_create_requires_confirmation_and_uses_domain_activity(action_stack):
    actor = action_stack["actor"]
    draft = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={
            "title": "Prepare demo dataset",
            "description": "Clean and verify data",
            "priority": 2,
        },
    )
    proposal = _create_proposal(action_stack, draft)

    assert action_stack["task"].list_tasks() == []
    assert action_stack["activity_repo"].list() == []

    result = action_stack["action_service"].confirm(actor, proposal.id)

    assert result.proposal.status == "EXECUTED"
    assert result.proposal.confirmed_by == actor.user_id
    assert result.proposal.confirmed_at is not None
    assert result.proposal.executed_at is not None
    assert result.proposal.error_code is None
    assert action_stack["task"].list_tasks()[0]["title"] == "Prepare demo dataset"
    assert action_stack["activity_repo"].list()[0]["event_type"] == "TASK_CREATED"


def test_task_assignment_and_lifecycle_execute_through_services(action_stack):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "Ship API", "", 3, assignee_id=actor.user_id
    )
    assign = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_assign",
        target_id=task_id,
        arguments={"assignee_id": "alice"},
    )
    action_stack["action_service"].confirm(
        actor,
        _create_proposal(action_stack, assign).id,
        eligible_member_ids={actor.user_id, "alice"},
    )
    assert action_stack["task"].get_task(actor, task_id)["assignee_id"] == "alice"

    leader = Actor(actor.user_id, Role.LEADER)
    start = action_stack["registry"].prepare(
        actor=leader,
        action_type="task_start",
        target_id=task_id,
        arguments={},
    )
    action_stack["action_service"].confirm(
        leader, _create_proposal(action_stack, start, actor=leader).id
    )
    complete = action_stack["registry"].prepare(
        actor=leader,
        action_type="task_complete",
        target_id=task_id,
        arguments={},
    )
    action_stack["action_service"].confirm(
        leader, _create_proposal(action_stack, complete, actor=leader).id
    )
    assert action_stack["task"].get_task(leader, task_id)["status"] == TaskStatus.DONE.value


def test_task_block_and_reopen_execute_through_services(action_stack):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "Blocked flow", "", 3, assignee_id=actor.user_id
    )
    action_stack["task"].start_task(actor, task_id)
    for action_type, expected_status in (
        ("task_block", TaskStatus.BLOCKED.value),
        ("task_reopen", TaskStatus.TODO.value),
    ):
        draft = action_stack["registry"].prepare(
            actor=actor,
            action_type=action_type,
            target_id=task_id,
            arguments={},
        )
        action_stack["action_service"].confirm(
            actor, _create_proposal(action_stack, draft).id
        )
        assert action_stack["task"].get_task(actor, task_id)["status"] == expected_status


def test_bug_assignment_transition_resolve_and_reopen(action_stack):
    actor = action_stack["actor"]
    bug_id = action_stack["bug"].report_bug(
        actor, "Crash", "", 4, assignee_id=actor.user_id
    )
    for action_type, arguments, expected_status in (
        ("bug_transition", {"to_status": "triaged"}, BugStatus.TRIAGED.value),
        ("bug_resolve", {}, BugStatus.RESOLVED.value),
        ("bug_reopen", {}, BugStatus.OPEN.value),
    ):
        draft = action_stack["registry"].prepare(
            actor=actor,
            action_type=action_type,
            target_id=bug_id,
            arguments=arguments,
        )
        action_stack["action_service"].confirm(
            actor, _create_proposal(action_stack, draft).id
        )
        assert action_stack["bug"].get_bug(actor, bug_id)["status"] == expected_status

    assignment = action_stack["registry"].prepare(
        actor=actor,
        action_type="bug_assign",
        target_id=bug_id,
        arguments={"assignee_id": "alice"},
    )
    action_stack["action_service"].confirm(
        actor,
        _create_proposal(action_stack, assignment).id,
        eligible_member_ids={actor.user_id, "alice"},
    )
    assert action_stack["bug"].get_bug(actor, bug_id)["assignee_id"] == "alice"


@pytest.mark.parametrize("role", [Role.MEMBER, Role.LEADER, Role.CO_LEAD])
def test_confirmation_ownership_cancel_and_replay(action_stack, role):
    actor = action_stack["actor"]
    other = Actor("other", role)
    draft = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Owned proposal"},
    )
    proposal = _create_proposal(action_stack, draft)

    with pytest.raises(AIActionOwnershipError):
        action_stack["action_service"].confirm(other, proposal.id)
    cancelled = action_stack["action_service"].cancel(actor, proposal.id)
    assert cancelled.proposal.status == "CANCELLED"
    assert cancelled.proposal.cancelled_at is not None
    assert action_stack["task"].list_tasks() == []
    with pytest.raises(AIActionAlreadyHandledError):
        action_stack["action_service"].confirm(actor, proposal.id)


def test_expired_proposal_cannot_execute(action_stack):
    draft = action_stack["registry"].prepare(
        actor=action_stack["actor"],
        action_type="task_create",
        target_id=None,
        arguments={"title": "Too late"},
    )
    proposal = _create_proposal(action_stack, draft)
    action_stack["clock"][0] += timedelta(seconds=61)

    with pytest.raises(AIActionExpiredError):
        action_stack["action_service"].confirm(action_stack["actor"], proposal.id)
    assert action_stack["action_repo"].get(proposal.id).status == "EXPIRED"
    assert action_stack["task"].list_tasks() == []


def test_expiration_boundary_and_cancel_are_persisted(action_stack):
    actor = action_stack["actor"]

    before = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Before expiry"},
    )
    before_proposal = _create_proposal(action_stack, before)
    action_stack["clock"][0] += timedelta(seconds=59)
    result = action_stack["action_service"].confirm(actor, before_proposal.id)
    assert result.proposal.status == "EXECUTED"

    at_boundary = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "At expiry"},
    )
    boundary_proposal = _create_proposal(action_stack, at_boundary)
    action_stack["clock"][0] += timedelta(seconds=60)
    with pytest.raises(AIActionExpiredError):
        action_stack["action_service"].confirm(actor, boundary_proposal.id)
    assert action_stack["action_repo"].get(boundary_proposal.id).status == "EXPIRED"

    after = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Cancel too late"},
    )
    after_proposal = _create_proposal(action_stack, after)
    action_stack["clock"][0] += timedelta(seconds=61)
    with pytest.raises(AIActionExpiredError):
        action_stack["action_service"].cancel(actor, after_proposal.id)
    assert action_stack["action_repo"].get(after_proposal.id).status == "EXPIRED"
    assert [task["title"] for task in action_stack["task"].list_tasks()] == [
        "Before expiry"
    ]


def test_terminal_proposals_cannot_revive(action_stack):
    actor = action_stack["actor"]
    draft = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "One execution"},
    )
    executed = action_stack["action_service"].confirm(
        actor, _create_proposal(action_stack, draft).id
    ).proposal

    with pytest.raises(AIActionAlreadyHandledError):
        action_stack["action_service"].confirm(actor, executed.id)
    assert action_stack["action_repo"].mark_executed(
        executed.id, action_stack["action_service"]._now_timestamp()
    ) is False
    assert len(action_stack["task"].list_tasks()) == 1


def test_permission_and_target_access_are_rechecked_at_confirmation(action_stack):
    leader = Actor(action_stack["actor"].user_id, Role.CO_LEAD)
    downgraded = Actor(action_stack["actor"].user_id, Role.MEMBER)
    creator = Actor("creator", Role.MEMBER)

    task_id = action_stack["task"].create_task(
        creator, "Restricted task", "", 3, assignee_id=creator.user_id
    )
    task_draft = action_stack["registry"].prepare(
        actor=leader,
        action_type="task_assign",
        target_id=task_id,
        arguments={"assignee_id": "alice"},
    )
    task_proposal = _create_proposal(action_stack, task_draft, actor=leader)
    with pytest.raises(PermissionDeniedError):
        action_stack["action_service"].confirm(
            downgraded,
            task_proposal.id,
            eligible_member_ids={"alice"},
        )
    assert action_stack["action_repo"].get(task_proposal.id).status == "FAILED"
    assert (
        action_stack["action_repo"].get(task_proposal.id).error_code
        == "PermissionDeniedError"
    )
    assert action_stack["task"].list_tasks()[0]["assignee_id"] == creator.user_id

    bug_id = action_stack["bug"].report_bug(
        creator, "Restricted bug", "", 4, assignee_id=creator.user_id
    )
    bug_draft = action_stack["registry"].prepare(
        actor=leader,
        action_type="bug_resolve",
        target_id=bug_id,
        arguments={},
    )
    bug_proposal = _create_proposal(action_stack, bug_draft, actor=leader)
    with pytest.raises(PermissionDeniedError):
        action_stack["action_service"].confirm(downgraded, bug_proposal.id)
    assert action_stack["action_repo"].get(bug_proposal.id).status == "FAILED"
    assert action_stack["bug"].list_bugs()[0]["status"] == BugStatus.OPEN.value


def test_removed_assignee_is_rejected_at_confirmation(action_stack):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "Membership drift", "", 3, assignee_id=actor.user_id
    )
    draft = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_assign",
        target_id=task_id,
        arguments={"assignee_id": "alice"},
    )
    proposal = _create_proposal(action_stack, draft)
    activity_count = len(action_stack["activity_repo"].list())

    with pytest.raises(AIActionValidationError, match="no longer an eligible"):
        action_stack["action_service"].confirm(
            actor,
            proposal.id,
            eligible_member_ids={actor.user_id},
        )

    assert action_stack["action_repo"].get(proposal.id).status == "FAILED"
    assert action_stack["task"].get_task(actor, task_id)["assignee_id"] == actor.user_id
    assert len(action_stack["activity_repo"].list()) == activity_count


def test_task_create_revalidates_assignee_and_same_assignment_is_idempotent(
    action_stack,
):
    actor = action_stack["actor"]
    create = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Assigned create", "assignee_id": "alice"},
    )
    create_proposal = _create_proposal(action_stack, create)
    with pytest.raises(AIActionValidationError, match="no longer an eligible"):
        action_stack["action_service"].confirm(
            actor,
            create_proposal.id,
            eligible_member_ids={actor.user_id},
        )
    assert action_stack["task"].list_tasks() == []

    task_id = action_stack["task"].create_task(
        actor, "Already assigned", "", 3, assignee_id="alice"
    )
    assign = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_assign",
        target_id=task_id,
        arguments={"assignee_id": "alice"},
    )
    proposal = _create_proposal(action_stack, assign)
    activity_count = len(action_stack["activity_repo"].list())
    result = action_stack["action_service"].confirm(
        actor,
        proposal.id,
        eligible_member_ids={actor.user_id, "alice"},
    )
    assert result.proposal.status == "EXECUTED"
    assert len(action_stack["activity_repo"].list()) == activity_count


def test_stale_task_and_bug_proposals_fail_without_duplicate_activity(action_stack):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "Task", "", 3, assignee_id=actor.user_id
    )
    action_stack["task"].start_task(actor, task_id)
    task_draft = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_complete",
        target_id=task_id,
        arguments={},
    )
    task_proposal = _create_proposal(action_stack, task_draft)
    action_stack["task"].block_task(actor, task_id)
    with pytest.raises(AIActionConflictError):
        action_stack["action_service"].confirm(actor, task_proposal.id)
    assert action_stack["task"].get_task(actor, task_id)["status"] == TaskStatus.BLOCKED.value
    assert action_stack["action_repo"].get(task_proposal.id).status == "FAILED"
    assert action_stack["action_repo"].get(task_proposal.id).error_code == "STALE_STATE"

    bug_id = action_stack["bug"].report_bug(
        actor, "Bug", "", 3, assignee_id=actor.user_id
    )
    bug_draft = action_stack["registry"].prepare(
        actor=actor,
        action_type="bug_resolve",
        target_id=bug_id,
        arguments={},
    )
    bug_proposal = _create_proposal(action_stack, bug_draft)
    action_stack["bug"].transition_status(actor, bug_id, BugStatus.TRIAGED.value)
    with pytest.raises(AIActionConflictError):
        action_stack["action_service"].confirm(actor, bug_proposal.id)
    assert action_stack["bug"].get_bug(actor, bug_id)["status"] == BugStatus.TRIAGED.value


def test_every_task_lifecycle_action_rejects_state_drift(action_stack):
    actor = action_stack["actor"]

    cases = []
    task_id = action_stack["task"].create_task(actor, "Start", "", 3, actor.user_id)
    cases.append(
        (
            "task_start",
            task_id,
            lambda target_id=task_id: action_stack["task"].block_task(
                actor, target_id
            ),
        )
    )

    task_id = action_stack["task"].create_task(actor, "Block", "", 3, actor.user_id)
    cases.append(
        (
            "task_block",
            task_id,
            lambda target_id=task_id: action_stack["task"].start_task(
                actor, target_id
            ),
        )
    )

    task_id = action_stack["task"].create_task(actor, "Complete", "", 3, actor.user_id)
    action_stack["task"].start_task(actor, task_id)
    cases.append(
        (
            "task_complete",
            task_id,
            lambda target_id=task_id: action_stack["task"].block_task(
                actor, target_id
            ),
        )
    )

    task_id = action_stack["task"].create_task(actor, "Reopen", "", 3, actor.user_id)
    action_stack["task"].start_task(actor, task_id)
    action_stack["task"].complete_task(actor, task_id)
    cases.append(
        (
            "task_reopen",
            task_id,
            lambda target_id=task_id: action_stack["task"].reopen_task(
                actor, target_id
            ),
        )
    )

    for action_type, target_id, drift in cases:
        draft = action_stack["registry"].prepare(
            actor=actor,
            action_type=action_type,
            target_id=target_id,
            arguments={},
        )
        proposal = _create_proposal(action_stack, draft)
        drift()
        activity_count = len(action_stack["activity_repo"].list())
        with pytest.raises(AIActionConflictError):
            action_stack["action_service"].confirm(actor, proposal.id)
        assert action_stack["action_repo"].get(proposal.id).status == "FAILED"
        assert len(action_stack["activity_repo"].list()) == activity_count


def test_every_bug_lifecycle_action_rejects_state_drift(action_stack):
    actor = action_stack["actor"]

    transition_id = action_stack["bug"].report_bug(actor, "Transition", "", 3, actor.user_id)
    transition = action_stack["registry"].prepare(
        actor=actor,
        action_type="bug_transition",
        target_id=transition_id,
        arguments={"to_status": BugStatus.IN_PROGRESS.value},
    )
    transition_proposal = _create_proposal(action_stack, transition)
    action_stack["bug"].resolve_bug(actor, transition_id)

    resolve_id = action_stack["bug"].report_bug(actor, "Resolve", "", 3, actor.user_id)
    resolve = action_stack["registry"].prepare(
        actor=actor,
        action_type="bug_resolve",
        target_id=resolve_id,
        arguments={},
    )
    resolve_proposal = _create_proposal(action_stack, resolve)
    action_stack["bug"].transition_status(actor, resolve_id, BugStatus.TRIAGED.value)

    reopen_id = action_stack["bug"].report_bug(actor, "Reopen", "", 3, actor.user_id)
    action_stack["bug"].resolve_bug(actor, reopen_id)
    reopen = action_stack["registry"].prepare(
        actor=actor,
        action_type="bug_reopen",
        target_id=reopen_id,
        arguments={},
    )
    reopen_proposal = _create_proposal(action_stack, reopen)
    action_stack["bug"].reopen_bug(actor, reopen_id)

    activity_count = len(action_stack["activity_repo"].list())
    for proposal in (transition_proposal, resolve_proposal, reopen_proposal):
        with pytest.raises(AIActionConflictError):
            action_stack["action_service"].confirm(actor, proposal.id)
        assert action_stack["action_repo"].get(proposal.id).status == "FAILED"
    assert len(action_stack["activity_repo"].list()) == activity_count


def test_success_cancel_expiry_and_failure_have_correct_activity(action_stack):
    actor = action_stack["actor"]

    success = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Audited once"},
    )
    success_proposal = _create_proposal(action_stack, success)
    before_success = len(action_stack["activity_repo"].list())
    action_stack["action_service"].confirm(actor, success_proposal.id)
    assert len(action_stack["activity_repo"].list()) == before_success + 1
    assert action_stack["action_repo"].get(success_proposal.id).status == "EXECUTED"

    cancelled = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Cancelled"},
    )
    cancelled_proposal = _create_proposal(action_stack, cancelled)
    action_stack["action_service"].cancel(actor, cancelled_proposal.id)

    expired = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Expired"},
    )
    expired_proposal = _create_proposal(action_stack, expired)
    action_stack["clock"][0] += timedelta(seconds=60)
    with pytest.raises(AIActionExpiredError):
        action_stack["action_service"].confirm(actor, expired_proposal.id)

    assert len(action_stack["activity_repo"].list()) == before_success + 1
    assert action_stack["action_repo"].get(cancelled_proposal.id).status == "CANCELLED"
    assert action_stack["action_repo"].get(expired_proposal.id).status == "EXPIRED"


def test_closed_session_invalidates_pending_proposals(action_stack):
    class StubAIService:
        async def answer_question(self, **kwargs):
            return GroundedAnswer("answer", [], [], "test")

    sessions = AISessionService(
        action_stack["session_repo"],
        StubAIService(),
        max_history_messages=4,
        action_service=action_stack["action_service"],
    )
    draft = action_stack["registry"].prepare(
        actor=action_stack["actor"],
        action_type="task_create",
        target_id=None,
        arguments={"title": "Never created"},
    )
    proposal = _create_proposal(action_stack, draft)

    sessions.close_session(action_stack["actor"], action_stack["session_id"])

    assert action_stack["action_repo"].get(proposal.id).status == "FAILED"
    with pytest.raises(AIActionAlreadyHandledError):
        action_stack["action_service"].confirm(action_stack["actor"], proposal.id)
    assert action_stack["task"].list_tasks() == []


def test_confirmation_is_independent_from_ai_session_busy_lock(action_stack):
    class StubAIService:
        async def answer_question(self, **kwargs):
            return GroundedAnswer("answer", [], [], "test")

    sessions = AISessionService(
        action_stack["session_repo"],
        StubAIService(),
        max_history_messages=4,
        action_service=action_stack["action_service"],
    )
    sessions._busy_sessions.add(action_stack["session_id"])
    draft = action_stack["registry"].prepare(
        actor=action_stack["actor"],
        action_type="task_create",
        target_id=None,
        arguments={"title": "Confirm while generating"},
    )
    proposal = _create_proposal(action_stack, draft)

    result = action_stack["action_service"].confirm(
        action_stack["actor"], proposal.id
    )

    assert result.proposal.status == "EXECUTED"
    assert action_stack["session_id"] in sessions._busy_sessions


def test_persisted_proposal_revalidates_after_service_restart(action_stack):
    draft = action_stack["registry"].prepare(
        actor=action_stack["actor"],
        action_type="task_create",
        target_id=None,
        arguments={"title": "Persisted proposal"},
    )
    proposal = _create_proposal(action_stack, draft)
    restarted_service = AIActionService(
        action_stack["action_repo"],
        action_stack["session_repo"],
        action_stack["registry"],
        expiration_seconds=60,
        clock=lambda: action_stack["clock"][0],
    )

    result = restarted_service.confirm(action_stack["actor"], proposal.id)

    assert result.proposal.status == "EXECUTED"
    assert action_stack["task"].list_tasks()[0]["title"] == "Persisted proposal"


def test_simultaneous_confirmation_executes_once(action_stack):
    draft = action_stack["registry"].prepare(
        actor=action_stack["actor"],
        action_type="task_create",
        target_id=None,
        arguments={"title": "Exactly once"},
    )
    proposal = _create_proposal(action_stack, draft)

    def confirm():
        try:
            return action_stack["action_service"].confirm(
                action_stack["actor"], proposal.id
            ).proposal.status
        except AIActionAlreadyHandledError:
            return "ALREADY_HANDLED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(), range(2)))

    assert sorted(results) == ["ALREADY_HANDLED", "EXECUTED"]
    assert len(action_stack["task"].list_tasks()) == 1


def test_provider_primary_and_fallback_produce_same_validated_draft(action_stack):
    actor = action_stack["actor"]
    action_stack["task"].create_task(
        actor, "API authentication", "", 3, assignee_id=actor.user_id
    )
    structured = (
        '{"kind":"action_proposal","action_type":"task_start",'
        '"target_query":"API authentication","arguments":{}}'
    )
    primary_provider = StaticProvider(structured)
    primary_interpreter = AIActionInterpreter(
        primary_provider,
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )
    fallback_provider = StaticProvider(structured)
    router = AIProviderRouter(
        StaticProvider(error=AITimeoutError("slow")),
        fallback_provider,
        fallback_enabled=True,
    )
    fallback_interpreter = AIActionInterpreter(
        router,
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )
    kwargs = {
        "actor": actor,
        "question": "Start the task",
        "action_type": "task_start",
        "history_messages": [],
        "known_members": [],
    }

    primary_draft = asyncio.run(primary_interpreter.interpret(**kwargs))
    fallback_draft = asyncio.run(fallback_interpreter.interpret(**kwargs))

    assert primary_draft == fallback_draft
    assert primary_draft.target_id == 1
    assert len(primary_provider.calls) == 1
    assert len(fallback_provider.calls) == 1
    assert "cannot execute actions" in primary_provider.calls[0]["system_instruction"]
    assert primary_provider.calls[0]["context_records"][0].source_id == "TASK-001"


@pytest.mark.parametrize(
    ("action_type", "question", "structured", "known_members"),
    [
        (
            "task_complete",
            "Complete the task",
            (
                '{"kind":"action_proposal","action_type":"task_complete",'
                '"target_query":"Provider task","arguments":{}}'
            ),
            [],
        ),
        (
            "task_assign",
            "Assign the task to Alice",
            (
                '{"kind":"action_proposal","action_type":"task_assign",'
                '"target_query":"Provider task",'
                '"arguments":{"assignee_name":"Alice"}}'
            ),
            [KnownMember("alice", "Alice", "alice")],
        ),
        (
            "bug_resolve",
            "Resolve the bug",
            (
                '{"kind":"action_proposal","action_type":"bug_resolve",'
                '"target_query":"Provider bug","arguments":{}}'
            ),
            [],
        ),
    ],
)
def test_provider_parity_for_supported_proposal_shapes(
    action_stack, action_type, question, structured, known_members
):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "Provider task", "", 3, assignee_id=actor.user_id
    )
    action_stack["task"].start_task(actor, task_id)
    action_stack["bug"].report_bug(
        actor, "Provider bug", "", 3, assignee_id=actor.user_id
    )
    providers = [StaticProvider(structured), StaticProvider(structured)]
    drafts = []
    for provider in providers:
        interpreter = AIActionInterpreter(
            provider,
            action_stack["registry"],
            action_stack["task"],
            action_stack["bug"],
            timeout_seconds=2,
        )
        drafts.append(
            asyncio.run(
                interpreter.interpret(
                    actor=actor,
                    question=question,
                    action_type=action_type,
                    history_messages=[],
                    known_members=known_members,
                )
            )
        )

    assert drafts[0] == drafts[1]
    assert len(providers[0].calls) == len(providers[1].calls) == 1


def test_malformed_fallback_output_cannot_create_or_execute_proposal(action_stack):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "Fallback task", "", 3, assignee_id=actor.user_id
    )
    action_stack["task"].start_task(actor, task_id)
    fallback = StaticProvider("not valid action JSON")
    router = AIProviderRouter(
        StaticProvider(error=AITimeoutError("primary unavailable")),
        fallback,
        fallback_enabled=True,
    )
    interpreter = AIActionInterpreter(
        router,
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )

    with pytest.raises(AIActionValidationError):
        asyncio.run(
            interpreter.interpret(
                actor=actor,
                question="Complete the task",
                action_type="task_complete",
                history_messages=[],
                known_members=[],
            )
        )

    assert len(fallback.calls) == 1
    with action_stack["db"].connect() as conn:
        proposal_count = conn.execute(
            "SELECT COUNT(*) FROM ai_action_proposals"
        ).fetchone()[0]
    assert proposal_count == 0
    assert action_stack["task"].get_task(actor, task_id)["status"] == "in_progress"


def test_confirmation_does_not_call_provider(action_stack):
    actor = action_stack["actor"]
    task_id = action_stack["task"].create_task(
        actor, "No provider on confirm", "", 3, assignee_id=actor.user_id
    )
    action_stack["task"].start_task(actor, task_id)
    provider = StaticProvider(
        '{"kind":"action_proposal","action_type":"task_complete",'
        '"target_query":"No provider on confirm","arguments":{}}'
    )
    interpreter = AIActionInterpreter(
        provider,
        action_stack["registry"],
        action_stack["task"],
        action_stack["bug"],
        timeout_seconds=2,
    )
    draft = asyncio.run(
        interpreter.interpret(
            actor=actor,
            question="Complete the task",
            action_type="task_complete",
            history_messages=[],
            known_members=[],
        )
    )
    proposal = _create_proposal(action_stack, draft)
    calls_before_confirm = len(provider.calls)

    result = action_stack["action_service"].confirm(actor, proposal.id)

    assert result.proposal.status == "EXECUTED"
    assert len(provider.calls) == calls_before_confirm


def test_full_session_flow_persists_then_confirms_action(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "container.db"))
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("AI_ACTION_EXPIRATION_SECONDS", "300")
    monkeypatch.setenv("GITHUB_ENABLED", "false")
    container = ServiceContainer(load_config())
    actor = Actor("owner", Role.MEMBER)
    task_id = container.task_service.create_task(
        actor, "Ship integration", "", 3, assignee_id=actor.user_id
    )
    container.task_service.start_task(actor, task_id)
    session = container.ai_session_service.create_session(actor, "thread-actions")

    answer = asyncio.run(
        container.ai_session_service.handle_message(
            actor=actor,
            session_id=session["id"],
            discord_thread_id="thread-actions",
            content=f"Complete TASK-{task_id:03d}",
            known_members=[KnownMember(actor.user_id, "Owner", "owner")],
        )
    )

    assert answer.retrieval_strategy == "action_proposal"
    assert answer.action_proposal is not None
    assert answer.action_proposal.status == "PENDING"
    assert answer.action_proposal.source_message_id is not None
    assert container.task_service.get_task(actor, task_id)["status"] == "in_progress"
    messages = container.ai_session_service.get_session_messages(actor, session["id"])
    assert [message["role"] for message in messages] == ["user", "assistant"]

    result = container.ai_action_service.confirm(actor, answer.action_proposal.id)

    assert result.proposal.status == "EXECUTED"
    assert container.task_service.get_task(actor, task_id)["status"] == "done"


def test_full_session_flow_keeps_informational_action_question_as_qa(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "container.db"))
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("GITHUB_ENABLED", "false")
    container = ServiceContainer(load_config())
    actor = Actor("owner", Role.MEMBER)
    task_id = container.task_service.create_task(
        actor, "Ship integration", "", 3, assignee_id=actor.user_id
    )
    session = container.ai_session_service.create_session(actor, "thread-qa")

    answer = asyncio.run(
        container.ai_session_service.handle_message(
            actor=actor,
            session_id=session["id"],
            discord_thread_id="thread-qa",
            content=f"How do I complete TASK-{task_id:03d}?",
        )
    )

    assert answer.retrieval_strategy != "action_proposal"
    assert answer.action_proposal is None
    assert container.task_service.get_task(actor, task_id)["status"] == "todo"
