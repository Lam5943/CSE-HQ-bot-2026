import json

from cse_hq_bot.db import Database


class ActivityRepository:
    def __init__(self, db: Database):
        self.db = db

    def append(
        self,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str,
        actor_id: str,
        metadata: dict | None = None,
    ) -> int:
        payload = json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True)
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO activities (event_type, entity_type, entity_id, actor_id, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_type, entity_type, entity_id, actor_id, payload),
            )
            return int(cur.lastrowid)

    def list(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        filters = (
            entity_type or None,
            entity_id or None,
            actor_id or None,
            event_type or None,
            start_time or None,
            end_time or None,
        )
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, event_type, entity_type, entity_id, actor_id, metadata, created_at
                FROM activities
                WHERE (? IS NULL OR entity_type = ?)
                  AND (? IS NULL OR entity_id = ?)
                  AND (? IS NULL OR actor_id = ?)
                  AND (? IS NULL OR event_type = ?)
                  AND (? IS NULL OR created_at >= ?)
                  AND (? IS NULL OR created_at <= ?)
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                OFFSET ?
                """,
                (*[item for value in filters for item in (value, value)], limit, max(offset, 0)),
            ).fetchall()
        return [self._deserialize(dict(row)) for row in rows]

    def _deserialize(self, row: dict) -> dict:
        row["metadata"] = json.loads(row.get("metadata") or "{}")
        return row
