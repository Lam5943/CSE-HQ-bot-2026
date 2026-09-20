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
                """
            )
            self._ensure_project_settings_columns(conn)

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
