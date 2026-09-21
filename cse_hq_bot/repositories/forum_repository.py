import sqlite3

from cse_hq_bot.db import Database


class ForumRepository:
    def __init__(self, db: Database):
        self.db = db

    def set_forums(self, mappings: dict[str, str], configured_by: str) -> None:
        with self.db.connect() as conn:
            for forum_kind, channel_id in mappings.items():
                conn.execute(
                    """
                    INSERT INTO forum_settings
                        (forum_kind, forum_channel_id, configured_by)
                    VALUES (?, ?, ?)
                    ON CONFLICT(forum_kind) DO UPDATE SET
                        forum_channel_id = excluded.forum_channel_id,
                        configured_by = excluded.configured_by,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (forum_kind, str(channel_id), configured_by),
                )

    def get_forum(self, forum_kind: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM forum_settings WHERE forum_kind = ?",
                (forum_kind,),
            ).fetchone()
        return dict(row) if row else None

    def list_forums(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM forum_settings ORDER BY forum_kind"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_publication(self, entity_type: str, entity_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM forum_publications
                WHERE entity_type = ? AND entity_id = ?
                """,
                (entity_type, str(entity_id)),
            ).fetchone()
        return dict(row) if row else None

    def create_publication(
        self,
        *,
        entity_type: str,
        entity_id: str,
        forum_channel_id: str,
        thread_id: str,
        starter_message_id: str,
    ) -> bool:
        try:
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO forum_publications
                        (entity_type, entity_id, forum_channel_id, thread_id,
                         starter_message_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entity_type,
                        str(entity_id),
                        str(forum_channel_id),
                        str(thread_id),
                        str(starter_message_id),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def touch_publication(self, entity_type: str, entity_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE forum_publications
                SET updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = ? AND entity_id = ?
                """,
                (entity_type, str(entity_id)),
            )

    def claim_delivery(self, delivery_id: str, event_type: str) -> bool:
        try:
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO github_webhook_deliveries
                        (delivery_id, event_type, status)
                    VALUES (?, ?, 'PROCESSING')
                    """,
                    (delivery_id, event_type),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def finish_delivery(
        self, delivery_id: str, status: str, error_code: str | None = None
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE github_webhook_deliveries
                SET status = ?, error_code = ?, processed_at = CURRENT_TIMESTAMP
                WHERE delivery_id = ?
                """,
                (status, error_code, delivery_id),
            )

    def get_delivery(self, delivery_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM github_webhook_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        return dict(row) if row else None
