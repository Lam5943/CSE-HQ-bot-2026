from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError


class BugRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        title: str,
        description: str,
        severity: int,
        created_by: str,
        assignee_id: str | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO bugs (title, description, status, severity, assignee_id, created_by)
                VALUES (?, ?, 'open', ?, ?, ?)
                """,
                (title, description, severity, assignee_id, created_by),
            )
            return int(cur.lastrowid)

    def list_all(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bugs ORDER BY severity DESC, id DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, bug_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM bugs WHERE id = ?", (bug_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Bug {bug_id} not found")
            return dict(row)

    def update(self, bug_id: int, fields: dict) -> None:
        if not fields:
            return
        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        if fields.get("status") == "resolved":
            assignments.append("resolved_at = CURRENT_TIMESTAMP")
        values.append(bug_id)
        with self.db.connect() as conn:
            cur = conn.execute(
                f"UPDATE bugs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"Bug {bug_id} not found")
