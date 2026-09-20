import sqlite3
from pathlib import Path

import pytest

from cse_hq_bot.db import Database
from cse_hq_bot.errors import InvalidTransitionError, NotFoundError, PermissionDeniedError
from cse_hq_bot.models import Actor, MeetingStatus, Role
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService


@pytest.fixture
def services(tmp_path: Path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    collab_repo = CollaborationRepository(db)
    task_repo = TaskRepository(db)
    return {
        "meeting": MeetingService(collab_repo),
        "decision": DecisionService(collab_repo),
        "standup": StandupService(collab_repo),
        "task": TaskService(task_repo),
    }


def test_meeting_service_create_list_get_participants_notes_and_lifecycle(services):
    leader = Actor("lead", Role.LEADER)
    member = Actor("member", Role.MEMBER)

    meeting_id = services["meeting"].create_meeting(
        leader,
        "Sprint Planning",
        "Plan the sprint",
        "Review backlog",
        "2026-09-21 09:00",
    )
    listed = services["meeting"].list_meetings(member)
    assert listed[0]["id"] == meeting_id
    assert listed[0]["code"] == "MEETING-001"

    services["meeting"].add_participant(leader, meeting_id, member.user_id)
    participants = services["meeting"].list_participants(member, meeting_id)
    assert [participant["user_id"] for participant in participants] == ["member"]

    services["meeting"].start_meeting(leader, meeting_id)
    note_id = services["meeting"].add_note(member, meeting_id, "Started backlog review.")
    assert note_id > 0
    notes = services["meeting"].get_notes(member, meeting_id)
    assert notes[0]["content"] == "Started backlog review."

    services["meeting"].edit_note(member, note_id, "Started backlog review and assignments.")
    notes = services["meeting"].get_notes(member, meeting_id)
    assert notes[0]["content"] == "Started backlog review and assignments."

    services["meeting"].complete_meeting(leader, meeting_id)
    meeting = services["meeting"].get_meeting(member, meeting_id)
    assert meeting["status"] == MeetingStatus.COMPLETED.value


def test_meeting_service_rejects_invalid_transitions_permissions_and_missing_records(services):
    leader = Actor("lead", Role.LEADER)
    member = Actor("member", Role.MEMBER)

    meeting_id = services["meeting"].create_meeting(
        leader, "Retro", "", "", "2026-09-21 10:00"
    )
    with pytest.raises(PermissionDeniedError):
        services["meeting"].start_meeting(member, meeting_id)
    with pytest.raises(PermissionDeniedError):
        services["meeting"].add_note(member, meeting_id, "I was not invited.")

    services["meeting"].start_meeting(leader, meeting_id)
    services["meeting"].complete_meeting(leader, meeting_id)
    with pytest.raises(InvalidTransitionError):
        services["meeting"].start_meeting(leader, meeting_id)
    with pytest.raises(NotFoundError):
        services["meeting"].get_meeting(member, 999)


def test_decision_service_create_get_edit_list_search_and_permissions(services):
    leader = Actor("lead", Role.LEADER)
    member = Actor("member", Role.MEMBER)
    meeting_id = services["meeting"].create_meeting(
        leader, "Architecture Review", "", "", "2026-09-22 11:00"
    )

    decision_id = services["decision"].create_decision(
        leader,
        title="Keep SQLite",
        decision="Use SQLite for v1 storage",
        context="Bot MVP",
        rationale="Small deployment footprint",
        alternatives="Postgres",
        meeting_id=meeting_id,
    )
    decision = services["decision"].get_decision(member, decision_id)
    assert decision["meeting_id"] == meeting_id
    assert decision["code"] == "DEC-001"

    services["decision"].edit_decision(
        leader,
        decision_id,
        rationale="Small deployment footprint and easy backups",
    )
    edited = services["decision"].get_decision(member, decision_id)
    assert "easy backups" in edited["rationale"]
    assert services["decision"].search_decisions(member, "sqlite")[0]["id"] == decision_id
    assert services["decision"].list_decisions_for_meeting(member, meeting_id)[0]["id"] == decision_id

    with pytest.raises(PermissionDeniedError):
        services["decision"].edit_decision(member, decision_id, title="Nope")
    with pytest.raises(NotFoundError):
        services["decision"].get_decision(member, 999)


def test_meeting_action_tasks_can_link_back_to_source_meeting(services):
    leader = Actor("lead", Role.LEADER)
    meeting_id = services["meeting"].create_meeting(
        leader, "Execution Sync", "", "", "2026-09-22 12:00"
    )

    task_id = services["task"].create_task(
        leader,
        "Follow up with QA",
        "Verify release blockers",
        3,
        source_meeting_id=meeting_id,
    )

    task = services["task"].get_task(leader, task_id)
    assert task["source_meeting_id"] == meeting_id


def test_standup_service_submission_upsert_reads_and_empty_blockers(services):
    member = Actor("member", Role.MEMBER)
    teammate = Actor("other", Role.MEMBER)

    first_id = services["standup"].submit_standup(
        member,
        previous="Finished UI",
        current="Write tests",
        blockers="",
        entry_date="2026-09-20",
    )
    second_id = services["standup"].submit_standup(
        member,
        previous="Finished UI and docs",
        current="Write tests",
        blockers="",
        entry_date="2026-09-20",
    )
    services["standup"].submit_standup(
        teammate,
        previous="Fixed bugs",
        current="Review PR",
        blockers="Need staging access",
        entry_date="2026-09-20",
    )

    assert first_id == second_id
    today = services["standup"].get_today(member, entry_date="2026-09-20")
    assert today is not None
    assert today["previous"] == "Finished UI and docs"
    assert today["blockers"] == ""

    team = services["standup"].list_for_date(member, "2026-09-20")
    assert {entry["user_id"] for entry in team} == {"member", "other"}

    recent = services["standup"].list_recent(member, days=7)
    assert len(recent) == 2


def test_database_initialize_migrates_existing_collaboration_schema(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE project_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO project_settings (id, name, description, updated_at)
        VALUES (1, 'Legacy', 'Legacy project', CURRENT_TIMESTAMP);
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            priority INTEGER NOT NULL,
            assignee_id TEXT,
            deadline TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );
        CREATE TABLE bugs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            severity INTEGER NOT NULL,
            assignee_id TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        );
        CREATE TABLE meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            meeting_date TEXT NOT NULL,
            created_by TEXT NOT NULL
        );
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER,
            summary TEXT NOT NULL,
            decided_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE standups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL,
            update_text TEXT NOT NULL,
            blockers TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO meetings (title, notes, meeting_date, created_by)
        VALUES ('Legacy meeting', 'Old notes', '2026-09-20', 'lead');
        INSERT INTO decisions (meeting_id, summary, decided_by)
        VALUES (1, 'Legacy decision', 'lead');
        INSERT INTO standups (member_id, update_text, blockers, created_at)
        VALUES ('member', 'Legacy update', '', '2026-09-20 08:00:00');
        """
    )
    conn.commit()
    conn.close()

    db = Database(str(db_path))
    db.initialize()

    with db.connect() as migrated:
        meeting = dict(migrated.execute("SELECT * FROM meetings WHERE id = 1").fetchone())
        decision = dict(migrated.execute("SELECT * FROM decisions WHERE id = 1").fetchone())
        standup = dict(migrated.execute("SELECT * FROM standups WHERE id = 1").fetchone())
        task_columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(tasks)").fetchall()
        }

    assert meeting["code"] == "MEETING-001"
    assert meeting["agenda"] == "Old notes"
    assert meeting["status"] == MeetingStatus.SCHEDULED.value
    assert decision["code"] == "DEC-001"
    assert decision["decision"] == "Legacy decision"
    assert standup["user_id"] == "member"
    assert standup["date"] == "2026-09-20"
    assert "source_meeting_id" in task_columns
