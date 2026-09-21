from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError

_UPDATE_STATEMENTS = {
    "title": "UPDATE bugs SET title = ? WHERE id = ?",
    "description": "UPDATE bugs SET description = ? WHERE id = ?",
    "status": "UPDATE bugs SET status = ? WHERE id = ?",
    "severity": "UPDATE bugs SET severity = ? WHERE id = ?",
    "assignee_id": "UPDATE bugs SET assignee_id = ? WHERE id = ?",
}


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
        with self.db.connect() as conn:
            for key, value in fields.items():
                statement = _UPDATE_STATEMENTS.get(key)
                if statement is None:
                    raise ValueError(f"Unsupported bug field: {key}")
                cur = conn.execute(statement, (value, bug_id))
                if cur.rowcount == 0:
                    raise NotFoundError(f"Bug {bug_id} not found")
            if fields.get("status") == "resolved":
                conn.execute(
                    "UPDATE bugs SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (bug_id,),
                )
