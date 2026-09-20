from cse_hq_bot.db import Database


class CollaborationRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_meeting(self, title: str, notes: str, meeting_date: str, created_by: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO meetings (title, notes, meeting_date, created_by) VALUES (?, ?, ?, ?)",
                (title, notes, meeting_date, created_by),
            )
            return int(cur.lastrowid)

    def create_decision(self, summary: str, decided_by: str, meeting_id: int | None = None) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO decisions (meeting_id, summary, decided_by) VALUES (?, ?, ?)",
                (meeting_id, summary, decided_by),
            )
            return int(cur.lastrowid)

    def create_standup(self, member_id: str, update_text: str, blockers: str = "") -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO standups (member_id, update_text, blockers) VALUES (?, ?, ?)",
                (member_id, update_text, blockers),
            )
            return int(cur.lastrowid)

    def list_recent_standups(self, days: int = 7) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM standups
                WHERE datetime(created_at) >= datetime('now', ?)
                ORDER BY created_at DESC
                """,
                (f"-{days} days",),
            ).fetchall()
            return [dict(row) for row in rows]
