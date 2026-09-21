import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from cse_hq_bot.ai.action_models import ActionIntentKind, KnownMember
from cse_hq_bot.ai.base import AIProviderResponse
from cse_hq_bot.ai.provider_router import AIProviderRouter
from cse_hq_bot.config import load_config
from cse_hq_bot.db import Database
from cse_hq_bot.errors import (
    AIActionAlreadyHandledError,
    AIActionConflictError,
    AIActionExpiredError,
    AIActionValidationError,
    AITimeoutError,
    PermissionDeniedError,
)
from cse_hq_bot.factory import ServiceContainer
from cse_hq_bot.models import Actor, MeetingStatus, Role
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.ai_action_repository import AIActionProposalRepository
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.ai_action_interpreter import (
    ActionIntentDetector,
    AIActionInterpreter,
)
from cse_hq_bot.services.ai_action_registry import AIActionRegistry
from cse_hq_bot.services.ai_action_service import AIActionService
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService


class StaticProvider:
    def __init__(self, text: str, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return AIProviderResponse(text=self.text)


@pytest.fixture
def v21_stack(tmp_path):
    db = Database(str(tmp_path / "actions-v21.db"))
    db.initialize()
    activity_repo = ActivityRepository(db)
    collab_repo = CollaborationRepository(db)
    task_service = TaskService(TaskRepository(db), activity_repo)
    bug_service = BugService(BugRepository(db), activity_repo)
    meeting_service = MeetingService(collab_repo, activity_repo)
    decision_service = DecisionService(collab_repo, activity_repo)
    standup_service = StandupService(collab_repo, activity_repo)
    clock = [datetime(2026, 9, 21, 8, 0, tzinfo=UTC)]
    registry = AIActionRegistry(
        task_service,
        bug_service,
        meeting_service,
        decision_service,
        standup_service,
        clock=lambda: clock[0],
    )
    session_repo = AISessionRepository(db)
    action_repo = AIActionProposalRepository(db)
    action_service = AIActionService(
        action_repo,
        session_repo,
        registry,
        expiration_seconds=60,
        clock=lambda: clock[0],
    )
    actor = Actor("leader", Role.LEADER)
    session_id = session_repo.create_session(actor.user_id, "thread-v21")
    return {
        "db": db,
        "activity_repo": activity_repo,
        "meeting": meeting_service,
        "decision": decision_service,
        "standup": standup_service,
        "registry": registry,
        "session_repo": session_repo,
        "action_repo": action_repo,
        "action_service": action_service,
        "actor": actor,
        "session_id": session_id,
        "clock": clock,
        "task": task_service,
        "bug": bug_service,
    }


def _proposal(stack, draft, actor=None):
    return stack["action_service"].create_proposal(
        actor=actor or stack["actor"],
        session_id=stack["session_id"],
        source_message_id=None,
        draft=draft,
    )


def _interpreter(stack, provider=None):
    return AIActionInterpreter(
        provider or StaticProvider("{}"),
        stack["registry"],
        stack["task"],
        stack["bug"],
        stack["meeting"],
        stack["decision"],
        stack["standup"],
        timeout_seconds=2,
    )


@pytest.mark.parametrize(
    ("question", "kind", "action_type"),
    [
        ("Complete MEETING-004", ActionIntentKind.SUPPORTED, "meeting_complete"),
        ("Add Alice to MEETING-004", ActionIntentKind.SUPPORTED, "meeting_add_participant"),
        ("Add a note to MEETING-004 that API approved", ActionIntentKind.SUPPORTED, "meeting_add_note"),
        ("Record a decision that we use SQLite", ActionIntentKind.SUPPORTED, "decision_create"),
        ("Edit DEC-007 rationale to simpler deploys", ActionIntentKind.SUPPORTED, "decision_edit"),
        ("Submit my standup", ActionIntentKind.SUPPORTED, "standup_submit"),
        ("Update my blocker to none", ActionIntentKind.SUPPORTED, "standup_update"),
        ("When is MEETING-004?", ActionIntentKind.NONE, None),
        ("What did we decide about SQLite?", ActionIntentKind.NONE, None),
        ("What did Alice write in today's standup?", ActionIntentKind.NONE, None),
        ("Can a completed meeting be reopened?", ActionIntentKind.NONE, None),
        ("Delete MEETING-004", ActionIntentKind.UNSUPPORTED, None),
        (
            "Add a note to MEETING-004 that the code deletion risk is accepted",
            ActionIntentKind.SUPPORTED,
            "meeting_add_note",
        ),
        (
            "Record a decision that we keep the code simple",
            ActionIntentKind.SUPPORTED,
            "decision_create",
        ),
        ("Update Alice's standup", ActionIntentKind.UNSUPPORTED, None),
        ("Create a meeting and add Alice", ActionIntentKind.MULTI_ACTION, None),
        ("Merge PR #12", ActionIntentKind.UNSUPPORTED, None),
    ],
)
def test_v21_intent_boundaries(question, kind, action_type):
    intent = ActionIntentDetector().detect(question)
    assert intent.kind == kind
    assert intent.action_type == action_type


def test_meeting_create_normalizes_time_and_waits_for_confirmation(v21_stack):
    actor = v21_stack["actor"]
    draft = asyncio.run(
        _interpreter(v21_stack).interpret(
            actor=actor,
            question="Create a sprint planning meeting tomorrow at 9",
            action_type="meeting_create",
            history_messages=[],
            known_members=[],
        )
    )
    proposal = _proposal(v21_stack, draft)

    assert draft.arguments["scheduled_at"] == "2026-09-22 09:00"
    assert "2026-09-22 09:00" in draft.summary
    assert v21_stack["meeting"].list_meetings(actor) == []

    before = len(v21_stack["activity_repo"].list())
    result = v21_stack["action_service"].confirm(actor, proposal.id)
    meetings = v21_stack["meeting"].list_meetings(actor)
    assert result.proposal.status == "EXECUTED"
    assert meetings[0]["title"] == "sprint planning"
    assert meetings[0]["scheduled_at"] == "2026-09-22 09:00"
    assert len(v21_stack["activity_repo"].list()) == before + 1


def test_meeting_lifecycle_and_stale_transition_use_domain_service(v21_stack):
    actor = v21_stack["actor"]
    meeting_id = v21_stack["meeting"].create_meeting(
        actor, "Lifecycle", "", "", "2026-09-22 09:00"
    )
    for action_type, expected in (
        ("meeting_start", MeetingStatus.IN_PROGRESS.value),
        ("meeting_complete", MeetingStatus.COMPLETED.value),
    ):
        draft = v21_stack["registry"].prepare(
            actor=actor,
            action_type=action_type,
            target_id=meeting_id,
            arguments={},
        )
        v21_stack["action_service"].confirm(actor, _proposal(v21_stack, draft).id)
        assert v21_stack["meeting"].get_meeting(actor, meeting_id)["status"] == expected

    cancelled_id = v21_stack["meeting"].create_meeting(
        actor, "Cancel", "", "", "2026-09-22 10:00"
    )
    cancel = v21_stack["registry"].prepare(
        actor=actor,
        action_type="meeting_cancel",
        target_id=cancelled_id,
        arguments={},
    )
    v21_stack["action_service"].confirm(actor, _proposal(v21_stack, cancel).id)
    assert v21_stack["meeting"].get_meeting(actor, cancelled_id)["status"] == "cancelled"

    stale_id = v21_stack["meeting"].create_meeting(
        actor, "Stale", "", "", "2026-09-22 11:00"
    )
    stale = v21_stack["registry"].prepare(
        actor=actor,
        action_type="meeting_start",
        target_id=stale_id,
        arguments={},
    )
    stale_proposal = _proposal(v21_stack, stale)
    v21_stack["meeting"].cancel_meeting(actor, stale_id)
    activity_count = len(v21_stack["activity_repo"].list())
    with pytest.raises(AIActionConflictError):
        v21_stack["action_service"].confirm(actor, stale_proposal.id)
    assert v21_stack["action_repo"].get(stale_proposal.id).status == "FAILED"
    assert len(v21_stack["activity_repo"].list()) == activity_count


def test_meeting_participant_resolution_and_membership_revalidation(v21_stack):
    actor = v21_stack["actor"]
    meeting_id = v21_stack["meeting"].create_meeting(
        actor, "Participants", "", "", "2026-09-22 09:00"
    )
    draft = asyncio.run(
        _interpreter(v21_stack).interpret(
            actor=actor,
            question=f"Add Alice to MEETING-{meeting_id:03d}",
            action_type="meeting_add_participant",
            history_messages=[],
            known_members=[KnownMember("alice", "Alice", "alice")],
        )
    )
    removed = _proposal(v21_stack, draft)
    with pytest.raises(AIActionValidationError, match="no longer an eligible"):
        v21_stack["action_service"].confirm(
            actor, removed.id, eligible_member_ids={actor.user_id}
        )
    assert v21_stack["meeting"].list_participants(actor, meeting_id) == []

    active = _proposal(v21_stack, draft)
    v21_stack["action_service"].confirm(
        actor,
        active.id,
        eligible_member_ids={actor.user_id, "alice"},
    )
    assert [
        item["user_id"]
        for item in v21_stack["meeting"].list_participants(actor, meeting_id)
    ] == ["alice"]


def test_meeting_note_preserves_user_text_and_permissions(v21_stack):
    actor = v21_stack["actor"]
    meeting_id = v21_stack["meeting"].create_meeting(
        actor, "Notes", "", "", "2026-09-22 09:00"
    )
    note_text = "The API schema is approved exactly as reviewed"
    draft = asyncio.run(
        _interpreter(v21_stack).interpret(
            actor=actor,
            question=f"Add a note to MEETING-{meeting_id:03d} that {note_text}",
            action_type="meeting_add_note",
            history_messages=[],
            known_members=[],
        )
    )
    v21_stack["action_service"].confirm(actor, _proposal(v21_stack, draft).id)
    assert v21_stack["meeting"].get_notes(actor, meeting_id)[0]["content"] == note_text

    member = Actor("member", Role.MEMBER)
    with pytest.raises(PermissionDeniedError):
        v21_stack["registry"].prepare(
            actor=member,
            action_type="meeting_add_note",
            target_id=meeting_id,
            arguments={"content": "Not a participant"},
        )


def test_meeting_ambiguous_title_requires_stable_choice(v21_stack):
    actor = v21_stack["actor"]
    for title in ("Sprint planning API", "Sprint planning UI"):
        v21_stack["meeting"].create_meeting(
            actor, title, "", "", "2026-09-22 09:00"
        )
    with pytest.raises(AIActionValidationError, match="Which record"):
        asyncio.run(
            _interpreter(v21_stack).interpret(
                actor=actor,
                question="Complete sprint planning meeting",
                action_type="meeting_complete",
                history_messages=[],
                known_members=[],
            )
        )


def test_missing_targets_invalid_lifecycle_and_permission_drift_are_safe(v21_stack):
    actor = v21_stack["actor"]
    for action_type, question in (
        ("meeting_complete", "Complete MEETING-999"),
        ("decision_edit", "Edit DEC-999 rationale to safer"),
    ):
        with pytest.raises(AIActionValidationError, match="unavailable"):
            asyncio.run(
                _interpreter(v21_stack).interpret(
                    actor=actor,
                    question=question,
                    action_type=action_type,
                    history_messages=[],
                    known_members=[],
                )
            )

    meeting_id = v21_stack["meeting"].create_meeting(
        actor, "Safety", "", "", "2026-09-22 09:00"
    )
    with pytest.raises(AIActionValidationError, match="Invalid meeting status"):
        v21_stack["registry"].prepare(
            actor=actor,
            action_type="meeting_complete",
            target_id=meeting_id,
            arguments={},
        )

    start = v21_stack["registry"].prepare(
        actor=actor,
        action_type="meeting_start",
        target_id=meeting_id,
        arguments={},
    )
    proposal = _proposal(v21_stack, start)
    with pytest.raises(PermissionDeniedError):
        v21_stack["action_service"].confirm(
            Actor(actor.user_id, Role.MEMBER), proposal.id
        )
    assert v21_stack["meeting"].get_meeting(actor, meeting_id)["status"] == "scheduled"


def test_decision_create_link_and_explicit_edit_preview(v21_stack):
    actor = v21_stack["actor"]
    meeting_id = v21_stack["meeting"].create_meeting(
        actor, "Architecture", "", "", "2026-09-22 09:00"
    )
    create = v21_stack["registry"].prepare(
        actor=actor,
        action_type="decision_create",
        target_id=None,
        arguments={
            "title": "Database choice",
            "decision": "Keep SQLite for v1",
            "context": "Local deployment",
            "rationale": "Operational simplicity",
            "alternatives": "Postgres",
            "meeting_id": f"MEETING-{meeting_id:03d}",
        },
    )
    v21_stack["action_service"].confirm(actor, _proposal(v21_stack, create).id)
    decision = v21_stack["decision"].list_decisions(actor)[0]
    assert decision["meeting_id"] == meeting_id

    edit = v21_stack["registry"].prepare(
        actor=actor,
        action_type="decision_edit",
        target_id=decision["id"],
        arguments={"rationale": "Operational simplicity and easier deployment"},
    )
    assert "OLD: Operational simplicity" in edit.summary
    assert "NEW: Operational simplicity and easier deployment" in edit.summary
    activity_count = len(v21_stack["activity_repo"].list())
    result = v21_stack["action_service"].confirm(actor, _proposal(v21_stack, edit).id)
    assert result.proposal.status == "EXECUTED"
    assert len(v21_stack["activity_repo"].list()) == activity_count + 1


def test_decision_stale_edit_and_permission_drift_fail(v21_stack):
    actor = v21_stack["actor"]
    decision_id = v21_stack["decision"].create_decision(
        actor, "Memory", "Original", "", "Old rationale", ""
    )
    stale = v21_stack["registry"].prepare(
        actor=actor,
        action_type="decision_edit",
        target_id=decision_id,
        arguments={"rationale": "Proposed rationale"},
    )
    stale_proposal = _proposal(v21_stack, stale)
    v21_stack["decision"].edit_decision(
        actor, decision_id, rationale="Another user's edit"
    )
    activity_count = len(v21_stack["activity_repo"].list())
    with pytest.raises(AIActionConflictError):
        v21_stack["action_service"].confirm(actor, stale_proposal.id)
    assert len(v21_stack["activity_repo"].list()) == activity_count

    create = v21_stack["registry"].prepare(
        actor=actor,
        action_type="decision_create",
        target_id=None,
        arguments={"title": "Denied", "decision": "No mutation"},
    )
    permission_proposal = _proposal(v21_stack, create)
    downgraded = Actor(actor.user_id, Role.MEMBER)
    with pytest.raises(PermissionDeniedError):
        v21_stack["action_service"].confirm(downgraded, permission_proposal.id)
    assert v21_stack["action_repo"].get(permission_proposal.id).status == "FAILED"


def test_standup_submit_update_and_empty_blockers(v21_stack):
    actor = v21_stack["actor"]
    submit = asyncio.run(
        _interpreter(v21_stack).interpret(
            actor=actor,
            question=(
                "Submit my standup: yesterday I finished auth, "
                "today I'm working on API tests, blocked by dataset access"
            ),
            action_type="standup_submit",
            history_messages=[],
            known_members=[],
        )
    )
    v21_stack["action_service"].confirm(actor, _proposal(v21_stack, submit).id)
    current = v21_stack["standup"].get_today(actor, "2026-09-21")
    assert current["previous"] == "finished auth"
    assert current["current"] == "working on API tests"
    assert current["blockers"] == "dataset access"

    update = asyncio.run(
        _interpreter(v21_stack).interpret(
            actor=actor,
            question="Update my blocker to none",
            action_type="standup_update",
            history_messages=[],
            known_members=[],
        )
    )
    v21_stack["action_service"].confirm(actor, _proposal(v21_stack, update).id)
    updated = v21_stack["standup"].get_today(actor, "2026-09-21")
    assert updated["id"] == current["id"]
    assert updated["blockers"] == ""


def test_standup_existing_and_cross_day_proposals_fail_safely(v21_stack):
    actor = v21_stack["actor"]
    v21_stack["clock"][0] = datetime(2026, 9, 21, 23, 59, 30, tzinfo=UTC)
    submit = v21_stack["registry"].prepare(
        actor=actor,
        action_type="standup_submit",
        target_id=None,
        arguments={"previous": "Done", "current": "Doing", "blockers": ""},
    )
    proposal = _proposal(v21_stack, submit)
    v21_stack["clock"][0] += timedelta(seconds=31)
    with pytest.raises(AIActionConflictError, match="date changed"):
        v21_stack["action_service"].confirm(actor, proposal.id)
    assert v21_stack["standup"].get_today(actor, "2026-09-21") is None

    v21_stack["clock"][0] = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    v21_stack["standup"].submit_standup(
        actor, "Existing", "Current", "", "2026-09-21"
    )
    with pytest.raises(AIActionValidationError, match="already exists"):
        v21_stack["registry"].prepare(
            actor=actor,
            action_type="standup_submit",
            target_id=None,
            arguments={"previous": "Duplicate", "current": "Duplicate"},
        )


def test_new_action_schemas_reject_unknown_or_cross_user_fields(v21_stack):
    actor = v21_stack["actor"]
    with pytest.raises(AIActionValidationError, match="Unsupported action arguments"):
        v21_stack["registry"].prepare(
            actor=actor,
            action_type="meeting_create",
            target_id=None,
            arguments={
                "title": "Unsafe",
                "scheduled_at": "2026-09-22 09:00",
                "command": "delete",
            },
        )
    with pytest.raises(AIActionValidationError, match="Unsupported action arguments"):
        v21_stack["registry"].prepare(
            actor=actor,
            action_type="standup_submit",
            target_id=None,
            arguments={
                "previous": "Done",
                "current": "Doing",
                "user_id": "alice",
            },
        )


def test_new_actions_keep_expiration_and_replay_guards(v21_stack):
    actor = v21_stack["actor"]
    expired = v21_stack["registry"].prepare(
        actor=actor,
        action_type="meeting_create",
        target_id=None,
        arguments={"title": "Expired", "scheduled_at": "2026-09-22 09:00"},
    )
    expired_proposal = _proposal(v21_stack, expired)
    v21_stack["clock"][0] += timedelta(seconds=60)
    with pytest.raises(AIActionExpiredError):
        v21_stack["action_service"].confirm(actor, expired_proposal.id)
    assert v21_stack["meeting"].list_meetings(actor) == []

    current = v21_stack["registry"].prepare(
        actor=actor,
        action_type="meeting_create",
        target_id=None,
        arguments={"title": "Once", "scheduled_at": "2026-09-22 10:00"},
    )
    current_proposal = _proposal(v21_stack, current)
    v21_stack["action_service"].confirm(actor, current_proposal.id)
    with pytest.raises(AIActionAlreadyHandledError):
        v21_stack["action_service"].confirm(actor, current_proposal.id)
    assert len(v21_stack["meeting"].list_meetings(actor)) == 1


@pytest.mark.parametrize(
    ("action_type", "question", "structured"),
    [
        (
            "meeting_create",
            "Schedule a meeting",
            (
                '{"kind":"action_proposal","action_type":"meeting_create",'
                '"arguments":{"title":"Planning","scheduled_at":"2026-09-22 09:00"}}'
            ),
        ),
        (
            "decision_create",
            "Record a decision",
            (
                '{"kind":"action_proposal","action_type":"decision_create",'
                '"arguments":{"title":"Database","decision":"Use SQLite"}}'
            ),
        ),
        (
            "standup_submit",
            "Submit my standup",
            (
                '{"kind":"action_proposal","action_type":"standup_submit",'
                '"arguments":{"previous":"Auth","current":"API tests","blockers":""}}'
            ),
        ),
    ],
)
def test_provider_parity_for_v21_actions(v21_stack, action_type, question, structured):
    actor = v21_stack["actor"]
    primary = StaticProvider(structured)
    fallback = StaticProvider(structured)
    providers = [
        primary,
        AIProviderRouter(
            StaticProvider("", error=AITimeoutError("primary unavailable")),
            fallback,
            fallback_enabled=True,
        ),
    ]
    drafts = []
    for provider in providers:
        drafts.append(
            asyncio.run(
                _interpreter(v21_stack, provider).interpret(
                    actor=actor,
                    question=question,
                    action_type=action_type,
                    history_messages=[],
                    known_members=[],
                )
            )
        )
    assert drafts[0] == drafts[1]
    assert len(primary.calls) == len(fallback.calls) == 1


def test_service_container_wires_v21_actions_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "container-v21.db"))
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("GITHUB_ENABLED", "false")
    container = ServiceContainer(load_config())
    actor = Actor("leader", Role.LEADER)
    session = container.ai_session_service.create_session(actor, "thread-v21-e2e")

    answer = asyncio.run(
        container.ai_session_service.handle_message(
            actor=actor,
            session_id=session["id"],
            discord_thread_id="thread-v21-e2e",
            content="Create a release planning meeting tomorrow at 9",
        )
    )

    assert answer.retrieval_strategy == "action_proposal"
    assert answer.action_proposal is not None
    assert container.meeting_service.list_meetings(actor) == []

    container.ai_action_service.confirm(actor, answer.action_proposal.id)

    meetings = container.meeting_service.list_meetings(actor)
    assert len(meetings) == 1
    assert meetings[0]["title"] == "release planning"
