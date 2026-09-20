from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError


class TaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        title: str,
        description: str,
        priority: int,
        created_by: str,
        assignee_id: str | None = None,
        deadline: str | None = None,
        source_meeting_id: int | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tasks (
                    title, description, status, priority, assignee_id, deadline, created_by, source_meeting_id
                )
                VALUES (?, ?, 'todo', ?, ?, ?, ?, ?)
                """,
                (title, description, priority, assignee_id, deadline, created_by, source_meeting_id),
            )
            return int(cur.lastrowid)

    def list_all(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY priority DESC, id DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, task_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Task {task_id} not found")
            return dict(row)

    def update(self, task_id: int, fields: dict) -> None:
        if not fields:
            return
        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        if fields.get("status") == "done":
            assignments.append("completed_at = CURRENT_TIMESTAMP")
        values.append(task_id)
        with self.db.connect() as conn:
            cur = conn.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"Task {task_id} not found")
