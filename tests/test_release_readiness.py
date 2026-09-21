import asyncio
import re
import sqlite3
import tomllib
from pathlib import Path

from cse_hq_bot.bot import CSEHQBot
from cse_hq_bot.config import load_config
from cse_hq_bot.db import Database
from cse_hq_bot.factory import ServiceContainer
from cse_hq_bot.repositories.forum_repository import ForumRepository
from cse_hq_bot.version import application_version

EXPECTED_ENVIRONMENT_VARIABLES = {
    "DISCORD_TOKEN",
    "AI_ENABLE_MESSAGE_CONTENT",
    "WELCOME_ENABLED",
    "WELCOME_CHANNEL_ID",
    "WEB_RESEARCH_ENABLED",
    "TAVILY_API_KEY",
    "WEB_RESEARCH_MAX_RESULTS",
    "WEB_RESEARCH_TIMEOUT",
    "DATABASE_PATH",
    "LOG_LEVEL",
    "AI_PROVIDER",
    "AI_MODEL",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GROQ_MAX_OUTPUT_TOKENS",
    "AI_MAX_CONTEXT_ITEMS",
    "AI_MAX_HISTORY_MESSAGES",
    "AI_REQUEST_TIMEOUT",
    "AI_PRIMARY_RETRIES",
    "AI_FALLBACK_ENABLED",
    "AI_FALLBACK_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "AI_ACTION_EXPIRATION_SECONDS",
    "GITHUB_ENABLED",
    "GITHUB_REPOSITORY_OWNER",
    "GITHUB_REPOSITORY_NAME",
    "GITHUB_TOKEN",
    "GITHUB_REQUEST_TIMEOUT",
    "GITHUB_CACHE_TTL",
    "GITHUB_MAX_RESULTS",
    "GITHUB_BUG_LABEL",
    "GITHUB_WEBHOOK_ENABLED",
    "GITHUB_WEBHOOK_SECRET",
    "WEBHOOK_HOST",
    "WEBHOOK_PORT",
    "GITHUB_WEBHOOK_PATH",
}


def test_release_version_and_environment_contract():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == "1.0.0"
    assert application_version() == "1.0.0"

    example = Path(".env.example").read_text(encoding="utf-8")
    configured = {
        line.split("=", 1)[0]
        for line in example.splitlines()
        if line and not line.startswith("#")
    }
    assert configured == EXPECTED_ENVIRONMENT_VARIABLES
    config_source = Path("cse_hq_bot/config.py").read_text(encoding="utf-8")
    loaded = set(re.findall(r'os\.getenv\(\s*"([A-Z0-9_]+)"', config_source))
    assert loaded == EXPECTED_ENVIRONMENT_VARIABLES
    assert "GEMINI_MODEL" not in example
    assert "FORUM_CHANNEL" not in example
    assert "your_" not in example.lower()


def test_fresh_database_and_command_surface_start_from_zero(tmp_path, monkeypatch):
    database_path = tmp_path / "fresh" / "cse-hq.db"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ENABLED", raising=False)
    monkeypatch.delenv("GITHUB_WEBHOOK_ENABLED", raising=False)
    monkeypatch.delenv("AI_FALLBACK_ENABLED", raising=False)

    container = ServiceContainer(load_config())
    assert database_path.exists()
    assert container.db.ping()

    async def audit_commands():
        bot = CSEHQBot(container)
        await bot.setup_hook()
        assert {command.name for command in bot.tree.get_commands()} == {
            "dashboard",
            "tasks",
            "bugs",
            "meetings",
            "decisions",
            "standup",
            "github",
            "weekly_report",
            "weekly_dashboard",
            "ai",
            "health",
            "setup",
        }
        setup = bot.tree.get_command("setup")
        assert setup is not None
        assert {command.name for command in setup.commands} == {"forums", "dashboard"}
        await bot.close()

    asyncio.run(audit_commands())


def test_representative_legacy_database_upgrade_is_additive_and_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE project_settings (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                description TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO project_settings VALUES (1, 'Legacy', 'Keep me', '2025-01-01');
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                priority INTEGER NOT NULL, assignee_id TEXT, deadline TEXT,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                completed_at TEXT
            );
            INSERT INTO tasks VALUES (1, 'Legacy task', '', 'todo', 2, NULL, NULL, 'u1', '2025-01-01', NULL);
            CREATE TABLE bugs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                severity INTEGER NOT NULL, assignee_id TEXT,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            INSERT INTO bugs VALUES (1, 'Legacy bug', '', 'open', 2, NULL, 'u1', '2025-01-01', NULL);
            CREATE TABLE meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '', meeting_date TEXT NOT NULL,
                created_by TEXT NOT NULL
            );
            INSERT INTO meetings VALUES (1, 'Legacy meeting', 'Agenda', '2025-01-02', 'u1');
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER,
                summary TEXT NOT NULL, decided_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO decisions VALUES (1, 1, 'Legacy decision', 'u1', '2025-01-03');
            CREATE TABLE standups (
                id INTEGER PRIMARY KEY AUTOINCREMENT, member_id TEXT NOT NULL,
                update_text TEXT NOT NULL, blockers TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            INSERT INTO standups VALUES (1, 'u1', 'Legacy standup', '', '2025-01-04');
            """
        )

    db = Database(str(path))
    db.initialize()
    db.initialize()

    with db.connect() as conn:
        assert conn.execute("SELECT name FROM project_settings").fetchone()[0] == "Legacy"
        assert conn.execute("SELECT title FROM tasks").fetchone()[0] == "Legacy task"
        assert conn.execute("SELECT title FROM bugs").fetchone()[0] == "Legacy bug"
        meeting = conn.execute("SELECT code, agenda FROM meetings").fetchone()
        decision = conn.execute("SELECT code, title FROM decisions").fetchone()
        standup = conn.execute("SELECT user_id, current FROM standups").fetchone()
        assert tuple(meeting) == ("MEETING-001", "Agenda")
        assert tuple(decision) == ("DEC-001", "Legacy decision")
        assert tuple(standup) == ("u1", "Legacy standup")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "activities",
            "ai_sessions",
            "ai_messages",
            "ai_action_proposals",
            "github_cache",
            "github_sync_state",
            "project_external_links",
            "forum_settings",
            "forum_publications",
            "dashboard_settings",
            "dashboard_publications",
            "github_webhook_deliveries",
        } <= tables
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM bugs").fetchone()[0] == 1


def test_restart_preserves_recovery_state_and_dedupe_guards(tmp_path):
    path = tmp_path / "restart.db"
    first = Database(str(path))
    first.initialize()
    with first.connect() as conn:
        conn.execute(
            "INSERT INTO ai_sessions (id, owner_id, discord_thread_id, status) VALUES (1, 'u1', 'thread-active', 'ACTIVE')"
        )
        conn.execute(
            "INSERT INTO ai_sessions (id, owner_id, discord_thread_id, status, closed_at) VALUES (2, 'u1', 'thread-closed', 'CLOSED', CURRENT_TIMESTAMP)"
        )
        conn.execute(
            """INSERT INTO ai_action_proposals
               (id, session_id, actor_id, action_type, arguments_json, summary,
                expected_state_json, status, created_at, expires_at)
               VALUES (1, 1, 'u1', 'task_create', '{}', 'pending', '{}',
                       'PENDING', '2026-01-01', '2099-01-01')"""
        )
        conn.execute(
            """INSERT INTO ai_action_proposals
               (id, session_id, actor_id, action_type, arguments_json, summary,
                expected_state_json, status, created_at, expires_at, error_code)
               VALUES (2, 2, 'u1', 'task_create', '{}', 'expired', '{}',
                       'EXPIRED', '2025-01-01', '2025-01-02', 'EXPIRED')"""
        )
        conn.execute(
            "INSERT INTO github_cache VALUES ('repository', 'repo', '{}', CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO github_sync_state VALUES ('all', 'SUCCESS', CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP)"
        )

    forum = ForumRepository(first)
    forum.set_forums(
        {"bug": "101", "pull_request": "102", "release": "103"}, "leader"
    )
    assert forum.create_publication(
        entity_type="bug",
        entity_id="BUG-001",
        forum_channel_id="101",
        thread_id="201",
        starter_message_id="301",
    )
    assert forum.claim_delivery("delivery-1", "issues")
    forum.finish_delivery("delivery-1", "PROCESSED")

    restarted = Database(str(path))
    restarted.initialize()
    restarted.initialize()
    restarted_forum = ForumRepository(restarted)

    with restarted.connect() as conn:
        sessions = conn.execute(
            "SELECT status FROM ai_sessions ORDER BY id"
        ).fetchall()
        proposals = conn.execute(
            "SELECT status FROM ai_action_proposals ORDER BY id"
        ).fetchall()
        assert [row[0] for row in sessions] == ["ACTIVE", "CLOSED"]
        assert [row[0] for row in proposals] == ["PENDING", "EXPIRED"]
        assert conn.execute("SELECT COUNT(*) FROM github_cache").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM forum_settings").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM forum_publications").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM github_webhook_deliveries").fetchone()[0] == 1

    assert not restarted_forum.create_publication(
        entity_type="bug",
        entity_id="BUG-001",
        forum_channel_id="101",
        thread_id="duplicate",
        starter_message_id="duplicate",
    )
    assert not restarted_forum.claim_delivery("delivery-1", "issues")
