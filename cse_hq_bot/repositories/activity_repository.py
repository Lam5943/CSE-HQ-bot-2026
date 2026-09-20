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
        clauses: list[str] = []
        values: list[object] = []
        if entity_type:
            clauses.append("entity_type = ?")
            values.append(entity_type)
        if entity_id:
            clauses.append("entity_id = ?")
            values.append(entity_id)
        if actor_id:
            clauses.append("actor_id = ?")
            values.append(actor_id)
        if event_type:
            clauses.append("event_type = ?")
            values.append(event_type)
        if start_time:
            clauses.append("created_at >= ?")
            values.append(start_time)
        if end_time:
            clauses.append("created_at <= ?")
            values.append(end_time)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend([limit, max(offset, 0)])
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, event_type, entity_type, entity_id, actor_id, metadata, created_at
                FROM activities
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                OFFSET ?
                """,
                values,
            ).fetchall()
        return [self._deserialize(dict(row)) for row in rows]

    def _deserialize(self, row: dict) -> dict:
        row["metadata"] = json.loads(row.get("metadata") or "{}")
        return row
