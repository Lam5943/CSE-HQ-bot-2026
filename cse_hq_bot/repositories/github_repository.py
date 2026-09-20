import json
import sqlite3

from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError


class GitHubRepositoryCache:
    def __init__(self, db: Database):
        self.db = db

    def replace_snapshot(self, snapshot: dict[str, list[tuple[str, dict]]]) -> None:
        with self.db.connect() as conn:
            for item_type, items in snapshot.items():
                conn.execute("DELETE FROM github_cache WHERE item_type = ?", (item_type,))
                conn.executemany(
                    """
                    INSERT INTO github_cache (item_type, external_id, data, synced_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [
                        (item_type, external_id, json.dumps(data, separators=(",", ":"), sort_keys=True))
                        for external_id, data in items
                    ],
                )

    def list_items(self, item_type: str, limit: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT data, synced_at
                FROM github_cache
                WHERE item_type = ?
                ORDER BY synced_at DESC, external_id DESC
                LIMIT ?
                """,
                (item_type, max(1, int(limit))),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get_item(self, item_type: str, external_id: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT data, synced_at FROM github_cache WHERE item_type = ? AND external_id = ?",
                (item_type, str(external_id)),
            ).fetchone()
        if not row:
            raise NotFoundError(f"Cached GitHub {item_type} {external_id} not found")
        return self._decode(row)

    def search_items(self, query: str, limit: int) -> list[dict]:
        needle = query.strip().lower()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT item_type, data, synced_at FROM github_cache ORDER BY synced_at DESC"
            ).fetchall()
        results: list[dict] = []
        for row in rows:
            item = self._decode(row)
            if needle and needle not in json.dumps(item, sort_keys=True).lower():
                continue
            item["item_type"] = row["item_type"]
            results.append(item)
            if len(results) >= max(1, int(limit)):
                break
        return results

    def set_sync_state(self, status: str, *, error: str | None = None, succeeded: bool = False) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO github_sync_state (scope, status, last_synced_at, error, updated_at)
                VALUES ('all', ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(scope) DO UPDATE SET
                    status = excluded.status,
                    last_synced_at = CASE
                        WHEN ? THEN CURRENT_TIMESTAMP
                        ELSE github_sync_state.last_synced_at
                    END,
                    error = excluded.error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (status, int(succeeded), error, int(succeeded)),
            )

    def get_sync_state(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT status, last_synced_at, error, updated_at FROM github_sync_state WHERE scope = 'all'"
            ).fetchone()
        return dict(row) if row else {
            "status": "NEVER_SYNCED",
            "last_synced_at": None,
            "error": None,
            "updated_at": None,
        }

    def create_link(
        self,
        *,
        entity_type: str,
        entity_id: int,
        external_type: str,
        external_id: str,
        created_by: str,
    ) -> dict:
        try:
            with self.db.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO project_external_links
                        (entity_type, entity_id, provider, external_type, external_id, created_by)
                    VALUES (?, ?, 'github', ?, ?, ?)
                    """,
                    (entity_type, int(entity_id), external_type, str(external_id), created_by),
                )
                link_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("This GitHub link already exists") from exc
        return self.get_link(link_id)

    def get_link(self, link_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_external_links WHERE id = ?",
                (int(link_id),),
            ).fetchone()
        if not row:
            raise NotFoundError(f"External link {link_id} not found")
        return dict(row)

    def list_links(self, entity_type: str | None = None, entity_id: int | None = None) -> list[dict]:
        query = "SELECT * FROM project_external_links WHERE provider = 'github'"
        values: list[object] = []
        if entity_type is not None:
            query += " AND entity_type = ?"
            values.append(entity_type)
        if entity_id is not None:
            query += " AND entity_id = ?"
            values.append(int(entity_id))
        query += " ORDER BY id DESC"
        with self.db.connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def delete_link(self, link_id: int) -> None:
        with self.db.connect() as conn:
            cursor = conn.execute("DELETE FROM project_external_links WHERE id = ?", (int(link_id),))
            if cursor.rowcount == 0:
                raise NotFoundError(f"External link {link_id} not found")

    def _decode(self, row) -> dict:
        data = json.loads(row["data"])
        data["synced_at"] = row["synced_at"]
        return data
