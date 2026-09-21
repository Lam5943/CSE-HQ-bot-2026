import json

from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError


class AISessionRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_session(self, owner_id: str, discord_thread_id: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_sessions (owner_id, discord_thread_id, status, created_at, last_active_at)
                VALUES (?, ?, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (owner_id, discord_thread_id),
            )
            return int(cur.lastrowid)

    def count_sessions_for_owner(self, owner_id: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM ai_sessions WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
        return int(row["count"])

    def list_sessions_for_owner(self, owner_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, owner_id, discord_thread_id, status, created_at, last_active_at, closed_at
                FROM ai_sessions
                WHERE owner_id = ? AND status != 'DELETED'
                ORDER BY last_active_at DESC, id DESC
                """,
                (owner_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, session_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT id, owner_id, discord_thread_id, status, created_at, last_active_at, closed_at
                FROM ai_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"AI session {session_id} not found")
        return dict(row)

    def get_session_by_thread_id(self, discord_thread_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT id, owner_id, discord_thread_id, status, created_at, last_active_at, closed_at
                FROM ai_sessions
                WHERE discord_thread_id = ?
                """,
                (discord_thread_id,),
            ).fetchone()
        return dict(row) if row else None

    def mark_session_deleted(self, session_id: int) -> None:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                UPDATE ai_sessions
                SET status = 'DELETED',
                    closed_at = COALESCE(closed_at, CURRENT_TIMESTAMP),
                    last_active_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (session_id,),
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"AI session {session_id} not found")

    def close_session(self, session_id: int) -> None:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                UPDATE ai_sessions
                SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP, last_active_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (session_id,),
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"AI session {session_id} not found")

    def touch_session(self, session_id: int) -> None:
        with self.db.connect() as conn:
            cur = conn.execute(
                "UPDATE ai_sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"AI session {session_id} not found")

    def create_message(self, session_id: int, role: str, content: str, source_refs: list[str] | None = None) -> int:
        payload = json.dumps(source_refs or [], separators=(",", ":"), sort_keys=True)
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_messages (session_id, role, content, source_refs, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (session_id, role, content, payload),
            )
            conn.execute(
                "UPDATE ai_sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            return int(cur.lastrowid)

    def list_messages(self, session_id: int, limit: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, source_refs, created_at
                FROM ai_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, max(1, int(limit))),
            ).fetchall()
        messages = [dict(row) for row in rows]
        for message in messages:
            message["source_refs"] = json.loads(message.get("source_refs") or "[]")
        messages.reverse()
        return messages
