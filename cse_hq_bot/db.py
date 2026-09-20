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
