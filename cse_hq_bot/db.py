import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def ping(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                INSERT OR IGNORE INTO project_settings (id, name, description, updated_at)
                VALUES (1, 'CSE-HQ Project', 'Default project dashboard', CURRENT_TIMESTAMP);

                CREATE TABLE IF NOT EXISTS tasks (
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

                CREATE TABLE IF NOT EXISTS bugs (
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

                CREATE TABLE IF NOT EXISTS meetings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    meeting_date TEXT NOT NULL,
                    created_by TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER,
                    summary TEXT NOT NULL,
                    decided_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(meeting_id) REFERENCES meetings(id)
                );

                CREATE TABLE IF NOT EXISTS standups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    member_id TEXT NOT NULL,
                    update_text TEXT NOT NULL,
                    blockers TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS meeting_participants (
                    meeting_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    added_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (meeting_id, user_id),
                    FOREIGN KEY(meeting_id) REFERENCES meetings(id)
                );

                CREATE TABLE IF NOT EXISTS meeting_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL,
                    author_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(meeting_id) REFERENCES meetings(id)
                );

                CREATE TABLE IF NOT EXISTS activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    discord_thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS ai_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_refs TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(session_id) REFERENCES ai_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS ai_action_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    actor_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_type TEXT,
                    target_id INTEGER,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    summary TEXT NOT NULL,
                    expected_state_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    source_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    confirmed_by TEXT,
                    executed_at TEXT,
                    cancelled_at TEXT,
                    error_code TEXT,
                    FOREIGN KEY(session_id) REFERENCES ai_sessions(id),
                    FOREIGN KEY(source_message_id) REFERENCES ai_messages(id)
                );

                CREATE TABLE IF NOT EXISTS github_cache (
                    item_type TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (item_type, external_id)
                );

                CREATE TABLE IF NOT EXISTS github_sync_state (
                    scope TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_synced_at TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS project_external_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    external_type TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(entity_type, entity_id, provider, external_type, external_id)
                );

                CREATE TABLE IF NOT EXISTS forum_settings (
                    forum_kind TEXT PRIMARY KEY,
                    forum_channel_id TEXT NOT NULL,
                    configured_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS forum_publications (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    forum_channel_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    starter_message_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (entity_type, entity_id)
                );

                CREATE TABLE IF NOT EXISTS dashboard_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    channel_id TEXT NOT NULL,
                    weekday INTEGER NOT NULL DEFAULT 0,
                    publish_time TEXT NOT NULL DEFAULT '09:00',
                    timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
                    configured_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS dashboard_publications (
                    week_key TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS github_webhook_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TEXT,
                    error_code TEXT
                );
                """
            )
            self._ensure_project_settings_columns(conn)
            self._ensure_tasks_columns(conn)
            self._ensure_meetings_columns(conn)
            self._ensure_decisions_columns(conn)
            self._ensure_standups_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_meeting_notes_meeting_id ON meeting_notes(meeting_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_standups_user_date ON standups(user_id, date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_meeting_id ON decisions(meeting_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activities_created_at ON activities(created_at, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activities_entity ON activities(entity_type, entity_id, created_at, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activities_actor ON activities(actor_id, created_at, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_sessions_owner ON ai_sessions(owner_id, last_active_at, id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_sessions_thread ON ai_sessions(discord_thread_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_messages_session ON ai_messages(session_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_action_session ON ai_action_proposals(session_id, status, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_action_owner ON ai_action_proposals(actor_id, status, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_github_cache_type ON github_cache(item_type, synced_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_external_links_entity ON project_external_links(entity_type, entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_forum_publications_thread ON forum_publications(thread_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dashboard_publications_published ON dashboard_publications(published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_received ON github_webhook_deliveries(received_at)"
            )

    def _ensure_project_settings_columns(self, conn: sqlite3.Connection) -> None:
        expected_columns = {
            "goal": "",
            "phase": "",
            "sprint": "",
            "deadline": "",
            "status": "",
        }
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(project_settings)").fetchall()
        }
        for column_name, default_value in expected_columns.items():
            if column_name in existing_columns:
                continue
            conn.execute(
                f"ALTER TABLE project_settings ADD COLUMN {column_name} TEXT NOT NULL DEFAULT '{default_value}'"
            )

    def _ensure_tasks_columns(self, conn: sqlite3.Connection) -> None:
        if not self._table_has_column(conn, "tasks", "source_meeting_id"):
            conn.execute("ALTER TABLE tasks ADD COLUMN source_meeting_id INTEGER")

    def _ensure_meetings_columns(self, conn: sqlite3.Connection) -> None:
        expected_columns = {
            "code": None,
            "description": "",
            "agenda": "",
            "status": "scheduled",
            "scheduled_at": None,
            "started_at": None,
            "ended_at": None,
            "created_at": None,
            "updated_at": None,
        }
        for column_name, default_value in expected_columns.items():
            self._add_text_column(conn, "meetings", column_name, default_value)
        conn.execute(
            """
            UPDATE meetings
            SET
                description = COALESCE(NULLIF(description, ''), ''),
                agenda = COALESCE(NULLIF(agenda, ''), notes, ''),
                status = COALESCE(NULLIF(status, ''), 'scheduled'),
                scheduled_at = COALESCE(scheduled_at, meeting_date),
                created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
                updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP),
                code = COALESCE(NULLIF(code, ''), printf('MEETING-%03d', id))
            """
        )

    def _ensure_decisions_columns(self, conn: sqlite3.Connection) -> None:
        expected_columns = {
            "code": None,
            "title": "",
            "decision": "",
            "context": "",
            "rationale": "",
            "alternatives": "",
            "created_by": None,
            "updated_at": None,
        }
        for column_name, default_value in expected_columns.items():
            self._add_text_column(conn, "decisions", column_name, default_value)
        conn.execute(
            """
            UPDATE decisions
            SET
                title = COALESCE(NULLIF(title, ''), summary, ''),
                decision = COALESCE(NULLIF(decision, ''), summary, ''),
                context = COALESCE(NULLIF(context, ''), ''),
                rationale = COALESCE(NULLIF(rationale, ''), ''),
                alternatives = COALESCE(NULLIF(alternatives, ''), ''),
                created_by = COALESCE(NULLIF(created_by, ''), decided_by),
                updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
                code = COALESCE(NULLIF(code, ''), printf('DEC-%03d', id))
            """
        )

    def _ensure_standups_columns(self, conn: sqlite3.Connection) -> None:
        expected_columns = {
            "user_id": None,
            "date": None,
            "previous": "",
            "current": "",
            "updated_at": None,
        }
        for column_name, default_value in expected_columns.items():
            self._add_text_column(conn, "standups", column_name, default_value)
        conn.execute(
            """
            UPDATE standups
            SET
                user_id = COALESCE(NULLIF(user_id, ''), member_id),
                date = COALESCE(date, substr(created_at, 1, 10), date('now')),
                previous = COALESCE(NULLIF(previous, ''), ''),
                current = COALESCE(NULLIF(current, ''), update_text, ''),
                blockers = COALESCE(blockers, ''),
                updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
            """
        )

    def _table_has_column(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        return column in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _add_text_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        default_value: str | None,
    ) -> None:
        if self._table_has_column(conn, table, column):
            return
        if default_value is None:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            return
        escaped_default = default_value.replace("'", "''")
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{escaped_default}'"
        )
