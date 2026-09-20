from cse_hq_bot.db import Database


class ProjectRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_settings(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT name, description, updated_at FROM project_settings WHERE id = 1"
            ).fetchone()
            return dict(row)

    def update_settings(self, name: str, description: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE project_settings
                SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (name, description),
            )

    def get_summary_counts(self) -> dict:
        with self.db.connect() as conn:
            task_total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            task_done = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'").fetchone()[0]
            bug_total = conn.execute("SELECT COUNT(*) FROM bugs").fetchone()[0]
            bug_open = conn.execute(
                "SELECT COUNT(*) FROM bugs WHERE status != 'resolved'"
            ).fetchone()[0]
            meetings_total = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        return {
            "task_total": task_total,
            "task_open": task_total - task_done,
            "task_done": task_done,
            "bug_total": bug_total,
            "bug_open": bug_open,
            "meetings_total": meetings_total,
        }
