from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError

_UPDATE_STATEMENTS = {
    "title": "UPDATE tasks SET title = ? WHERE id = ?",
    "description": "UPDATE tasks SET description = ? WHERE id = ?",
    "status": "UPDATE tasks SET status = ? WHERE id = ?",
    "priority": "UPDATE tasks SET priority = ? WHERE id = ?",
    "assignee_id": "UPDATE tasks SET assignee_id = ? WHERE id = ?",
    "deadline": "UPDATE tasks SET deadline = ? WHERE id = ?",
}


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
        with self.db.connect() as conn:
            for key, value in fields.items():
                statement = _UPDATE_STATEMENTS.get(key)
                if statement is None:
                    raise ValueError(f"Unsupported task field: {key}")
                cur = conn.execute(statement, (value, task_id))
                if cur.rowcount == 0:
                    raise NotFoundError(f"Task {task_id} not found")
            if fields.get("status") == "done":
                conn.execute(
                    "UPDATE tasks SET completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (task_id,),
                )
