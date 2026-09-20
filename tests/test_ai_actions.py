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
    action_stack["action_service"].confirm(actor, _create_proposal(action_stack, assign).id)
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
        actor, _create_proposal(action_stack, assignment).id
    )
    assert action_stack["bug"].get_bug(actor, bug_id)["assignee_id"] == "alice"


def test_confirmation_ownership_cancel_and_replay(action_stack):
    actor = action_stack["actor"]
    other = Actor("other", Role.LEADER)
    draft = action_stack["registry"].prepare(
        actor=actor,
        action_type="task_create",
        target_id=None,
        arguments={"title": "Owned proposal"},
    )
    proposal = _create_proposal(action_stack, draft)

    with pytest.raises(PermissionDeniedError):
        action_stack["action_service"].confirm(other, proposal.id)
    cancelled = action_stack["action_service"].cancel(actor, proposal.id)
    assert cancelled.proposal.status == "CANCELLED"
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
