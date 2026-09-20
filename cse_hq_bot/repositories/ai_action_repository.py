import json

from cse_hq_bot.ai.action_models import ActionProposal, ActionProposalDraft
from cse_hq_bot.db import Database
from cse_hq_bot.errors import NotFoundError


class AIActionProposalRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        *,
        session_id: int,
        actor_id: str,
        draft: ActionProposalDraft,
        source_message_id: int | None,
        created_at: str,
        expires_at: str,
    ) -> int:
        arguments = json.dumps(draft.arguments, separators=(",", ":"), sort_keys=True)
        expected_state = json.dumps(
            draft.expected_state, separators=(",", ":"), sort_keys=True
        )
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_action_proposals (
                    session_id, actor_id, action_type, target_type, target_id,
                    arguments_json, summary, expected_state_json, status,
                    source_message_id, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
                """,
                (
                    session_id,
                    actor_id,
                    draft.action_type,
                    draft.target_type,
                    draft.target_id,
                    arguments,
                    draft.summary,
                    expected_state,
                    source_message_id,
                    created_at,
                    expires_at,
                ),
            )
            return int(cur.lastrowid)

    def get(self, proposal_id: int) -> ActionProposal:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_action_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"AI action proposal {proposal_id} not found")
        return self._deserialize(dict(row))

    def expire_pending(self, proposal_id: int, now: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'EXPIRED', error_code = 'EXPIRED'
                WHERE id = ? AND status = 'PENDING' AND expires_at <= ?
                """,
                (proposal_id, now),
            )
            return cur.rowcount == 1

    def claim_for_confirmation(
        self, proposal_id: int, actor_id: str, now: str
    ) -> bool:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'EXPIRED', error_code = 'EXPIRED'
                WHERE id = ? AND status = 'PENDING' AND expires_at <= ?
                """,
                (proposal_id, now),
            )
            cur = conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'CONFIRMED', confirmed_at = ?, confirmed_by = ?
                WHERE id = ?
                  AND actor_id = ?
                  AND status = 'PENDING'
                  AND expires_at > ?
                  AND EXISTS (
                      SELECT 1 FROM ai_sessions
                      WHERE ai_sessions.id = ai_action_proposals.session_id
                        AND ai_sessions.status = 'ACTIVE'
                  )
                """,
                (now, actor_id, proposal_id, actor_id, now),
            )
            return cur.rowcount == 1

    def cancel_pending(self, proposal_id: int, actor_id: str, now: str) -> bool:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'EXPIRED', error_code = 'EXPIRED'
                WHERE id = ? AND status = 'PENDING' AND expires_at <= ?
                """,
                (proposal_id, now),
            )
            cur = conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'CANCELLED', cancelled_at = ?
                WHERE id = ? AND actor_id = ? AND status = 'PENDING'
                """,
                (now, proposal_id, actor_id),
            )
            return cur.rowcount == 1

    def mark_executed(self, proposal_id: int, executed_at: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'EXECUTED', executed_at = ?, error_code = NULL
                WHERE id = ? AND status = 'CONFIRMED'
                """,
                (executed_at, proposal_id),
            )

    def mark_failed(self, proposal_id: int, error_code: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'FAILED', error_code = ?
                WHERE id = ? AND status = 'CONFIRMED'
                """,
                (error_code, proposal_id),
            )

    def fail_pending_for_session(self, session_id: int) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                UPDATE ai_action_proposals
                SET status = 'FAILED', error_code = 'SESSION_CLOSED'
                WHERE session_id = ? AND status = 'PENDING'
                """,
                (session_id,),
            )
            return cur.rowcount

    def _deserialize(self, row: dict) -> ActionProposal:
        return ActionProposal(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            actor_id=str(row["actor_id"]),
            action_type=str(row["action_type"]),
            target_type=row.get("target_type"),
            target_id=int(row["target_id"]) if row.get("target_id") is not None else None,
            arguments=json.loads(row.get("arguments_json") or "{}"),
            summary=str(row["summary"]),
            expected_state=json.loads(row.get("expected_state_json") or "{}"),
            status=str(row["status"]),
            source_message_id=(
                int(row["source_message_id"])
                if row.get("source_message_id") is not None
                else None
            ),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            confirmed_at=row.get("confirmed_at"),
            confirmed_by=row.get("confirmed_by"),
            executed_at=row.get("executed_at"),
            cancelled_at=row.get("cancelled_at"),
            error_code=row.get("error_code"),
        )
