import sqlite3

from cse_hq_bot.db import Database


class DashboardPublicationRepository:
    def __init__(self, db: Database):
        self.db = db

    def set_settings(
        self,
        *,
        channel_id: str,
        weekday: int,
        publish_time: str,
        timezone: str,
        configured_by: str,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dashboard_settings
                    (id, channel_id, weekday, publish_time, timezone, configured_by)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    weekday = excluded.weekday,
                    publish_time = excluded.publish_time,
                    timezone = excluded.timezone,
                    configured_by = excluded.configured_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(channel_id), int(weekday), publish_time, timezone, configured_by),
            )

    def get_settings(self) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dashboard_settings WHERE id = 1"
            ).fetchone()
        return dict(row) if row else None

    def get_publication(self, week_key: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dashboard_publications WHERE week_key = ?",
                (week_key,),
            ).fetchone()
        return dict(row) if row else None

    def create_publication(
        self,
        *,
        week_key: str,
        channel_id: str,
        message_id: str,
        snapshot_json: str,
    ) -> bool:
        try:
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dashboard_publications
                        (week_key, channel_id, message_id, snapshot_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (week_key, str(channel_id), str(message_id), snapshot_json),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def upsert_publication(
        self,
        *,
        week_key: str,
        channel_id: str,
        message_id: str,
        snapshot_json: str,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dashboard_publications
                    (week_key, channel_id, message_id, snapshot_json, published_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(week_key) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_id = excluded.message_id,
                    snapshot_json = excluded.snapshot_json,
                    published_at = CURRENT_TIMESTAMP
                """,
                (week_key, str(channel_id), str(message_id), snapshot_json),
            )

    def list_recent_publications(self, limit: int = 12) -> list[dict]:
        safe_limit = max(1, min(int(limit), 52))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dashboard_publications
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]
