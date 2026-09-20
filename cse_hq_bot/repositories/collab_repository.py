from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError


class CollaborationRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_meeting(
        self,
        *,
        title: str,
        description: str,
        agenda: str,
        status: str,
        created_by: str,
        scheduled_at: str,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO meetings (
                    title, notes, meeting_date, created_by, description, agenda, status, scheduled_at, created_at, updated_at
                )
                VALUES (?, '', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (title, scheduled_at, created_by, description, agenda, status, scheduled_at),
            )
            meeting_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE meetings SET code = printf('MEETING-%03d', id) WHERE id = ?",
                (meeting_id,),
            )
            return meeting_id

    def list_meetings(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM meetings ORDER BY COALESCE(scheduled_at, meeting_date) DESC, id DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_meeting(self, meeting_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Meeting {meeting_id} not found")
            return dict(row)

    def update_meeting(self, meeting_id: int, fields: dict) -> None:
        self._update_row("meetings", "Meeting", meeting_id, fields)

    def add_meeting_participant(self, meeting_id: int, user_id: str, added_by: str) -> bool:
        self.get_meeting(meeting_id)
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO meeting_participants (meeting_id, user_id, added_by)
                VALUES (?, ?, ?)
                """,
                (meeting_id, user_id, added_by),
            )
            return cur.rowcount > 0

    def remove_meeting_participant(self, meeting_id: int, user_id: str) -> None:
        self.get_meeting(meeting_id)
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM meeting_participants WHERE meeting_id = ? AND user_id = ?",
                (meeting_id, user_id),
            )

    def list_meeting_participants(self, meeting_id: int) -> list[dict]:
        self.get_meeting(meeting_id)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT meeting_id, user_id, added_by, created_at
                FROM meeting_participants
                WHERE meeting_id = ?
                ORDER BY user_id
                """,
                (meeting_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_meeting_note(self, meeting_id: int, author_id: str, content: str) -> int:
        self.get_meeting(meeting_id)
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO meeting_notes (meeting_id, author_id, content, created_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (meeting_id, author_id, content),
            )
            return int(cur.lastrowid)

    def list_meeting_notes(self, meeting_id: int) -> list[dict]:
        self.get_meeting(meeting_id)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM meeting_notes
                WHERE meeting_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (meeting_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_meeting_note(self, note_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM meeting_notes WHERE id = ?", (note_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Meeting note {note_id} not found")
            return dict(row)

    def update_meeting_note(self, note_id: int, fields: dict) -> None:
        self._update_row("meeting_notes", "Meeting note", note_id, fields)

    def create_decision(
        self,
        *,
        title: str,
        decision: str,
        context: str,
        rationale: str,
        alternatives: str,
        created_by: str,
        meeting_id: int | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO decisions (
                    meeting_id, summary, decided_by, created_at, title, decision, context, rationale, alternatives, created_by, updated_at
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    meeting_id,
                    decision,
                    created_by,
                    title,
                    decision,
                    context,
                    rationale,
                    alternatives,
                    created_by,
                ),
            )
            decision_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE decisions SET code = printf('DEC-%03d', id) WHERE id = ?",
                (decision_id,),
            )
            return decision_id

    def list_decisions(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM decisions ORDER BY created_at DESC, id DESC").fetchall()
            return [dict(row) for row in rows]

    def get_decision(self, decision_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Decision {decision_id} not found")
            return dict(row)

    def update_decision(self, decision_id: int, fields: dict) -> None:
        self._update_row("decisions", "Decision", decision_id, fields)

    def create_standup(
        self,
        *,
        user_id: str,
        entry_date: str,
        previous: str,
        current: str,
        blockers: str,
    ) -> int:
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM standups WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1",
                (user_id, entry_date),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE standups
                    SET
                        member_id = ?,
                        update_text = ?,
                        blockers = ?,
                        user_id = ?,
                        date = ?,
                        previous = ?,
                        current = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (user_id, current, blockers, user_id, entry_date, previous, current, existing["id"]),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO standups (
                    member_id, update_text, blockers, created_at, user_id, date, previous, current, updated_at
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (user_id, current, blockers, user_id, entry_date, previous, current),
            )
            return int(cur.lastrowid)

    def list_standups(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM standups ORDER BY date DESC, id DESC").fetchall()
            return [dict(row) for row in rows]

    def get_standup(self, standup_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM standups WHERE id = ?", (standup_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Standup {standup_id} not found")
            return dict(row)

    def get_standup_for_user_date(self, user_id: str, entry_date: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM standups WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1",
                (user_id, entry_date),
            ).fetchone()
            return dict(row) if row else None

    def list_recent_standups(self, days: int = 7) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM standups
                WHERE date >= date('now', ?)
                ORDER BY date DESC, id DESC
                """,
                (f"-{days - 1} days",),
            ).fetchall()
            return [dict(row) for row in rows]

    def _update_row(self, table: str, label: str, row_id: int, fields: dict) -> None:
        if not fields:
            return
        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        if table in {"meetings", "decisions", "meeting_notes", "standups"}:
            assignments.append("updated_at = CURRENT_TIMESTAMP")
        values.append(row_id)
        with self.db.connect() as conn:
            cur = conn.execute(
                f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"{label} {row_id} not found")
