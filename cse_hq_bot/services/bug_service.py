import logging
from collections.abc import Callable
from typing import ClassVar

from cse_hq_bot.errors import InvalidTransitionError, PermissionDeniedError
from cse_hq_bot.identifiers import bug_code
from cse_hq_bot.models import Actor, BugStatus, Role
from cse_hq_bot.permissions import ensure_can_modify_bug
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.bug_repository import BugRepository

logger = logging.getLogger(__name__)


class BugService:
    _TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        BugStatus.OPEN.value: {
            BugStatus.TRIAGED.value,
            BugStatus.IN_PROGRESS.value,
            BugStatus.RESOLVED.value,
        },
        BugStatus.TRIAGED.value: {
            BugStatus.IN_PROGRESS.value,
            BugStatus.RESOLVED.value,
            BugStatus.OPEN.value,
        },
        BugStatus.IN_PROGRESS.value: {
            BugStatus.RESOLVED.value,
            BugStatus.TRIAGED.value,
            BugStatus.OPEN.value,
        },
        BugStatus.RESOLVED.value: {
            BugStatus.OPEN.value,
        },
    }

    def __init__(
        self,
        repo: BugRepository,
        activity_repo: ActivityRepository | None = None,
        publication_hook: Callable[[str, dict], None] | None = None,
    ):
        self.repo = repo
        self.activity_repo = activity_repo
        self.publication_hook = publication_hook

    def report_bug(
        self,
        actor: Actor,
        title: str,
        description: str,
        severity: int,
        assignee_id: str | None = None,
    ) -> int:
        bug_id = self.repo.create(
            title=title,
            description=description,
            severity=severity,
            created_by=actor.user_id,
            assignee_id=assignee_id,
        )
        bug = self.repo.get(bug_id)
        self._append_activity(
            "BUG_REPORTED",
            bug,
            actor.user_id,
            {
                "row_id": bug_id,
                "severity": bug["severity"],
                "assignee_id": bug.get("assignee_id"),
            },
        )
        self._publish("reported", bug)
        return bug_id

    def list_bugs(self) -> list[dict]:
        return self.repo.list_all()

    def _can_access(self, actor: Actor, bug: dict) -> bool:
        if actor.role in {Role.LEADER, Role.CO_LEAD}:
            return True
        return actor.user_id in {bug.get("assignee_id"), bug.get("created_by")}

    def list_accessible_bugs(self, actor: Actor) -> list[dict]:
        return [bug for bug in self.list_bugs() if self._can_access(actor, bug)]

    def list_open_bugs(self, actor: Actor) -> list[dict]:
        return [bug for bug in self.list_accessible_bugs(actor) if bug["status"] != BugStatus.RESOLVED.value]

    def get_bug(self, actor: Actor, bug_id: int) -> dict:
        bug = self.repo.get(bug_id)
        if not self._can_access(actor, bug):
            raise PermissionDeniedError("You are not allowed to view this bug")
        return bug

    def filter_bugs(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        severity: int | None = None,
        assignee_id: str | None = None,
        reporter_id: str | None = None,
        open_only: bool = False,
        search: str | None = None,
    ) -> list[dict]:
        bugs = self.list_open_bugs(actor) if open_only else self.list_accessible_bugs(actor)
        if status:
            status_value = BugStatus(status).value
            bugs = [bug for bug in bugs if bug["status"] == status_value]
        if severity is not None:
            bugs = [bug for bug in bugs if int(bug["severity"]) == int(severity)]
        if assignee_id:
            bugs = [bug for bug in bugs if bug.get("assignee_id") == assignee_id]
        if reporter_id:
            bugs = [bug for bug in bugs if bug.get("created_by") == reporter_id]
        if search:
            needle = search.strip().lower()
            bugs = [
                bug
                for bug in bugs
                if needle in bug["title"].lower() or needle in bug["description"].lower()
            ]
        return bugs

    def bug_statistics(self, actor: Actor) -> dict:
        bugs = self.list_accessible_bugs(actor)
        status_counts = {status.value: 0 for status in BugStatus}
        for bug in bugs:
            status_counts[bug["status"]] = status_counts.get(bug["status"], 0) + 1
        return {
            "total": len(bugs),
            "open": len([bug for bug in bugs if bug["status"] != BugStatus.RESOLVED.value]),
            "by_status": status_counts,
            "critical": len([bug for bug in bugs if int(bug["severity"]) >= 4]),
            "unassigned": len([bug for bug in bugs if not bug.get("assignee_id")]),
        }

    def _ensure_transition(self, current_status: str, target_status: str) -> None:
        if target_status not in self._TRANSITIONS.get(current_status, set()):
            raise InvalidTransitionError(f"Invalid bug status transition: {current_status} -> {target_status}")

    def update_bug(self, actor: Actor, bug_id: int, **fields: object) -> None:
        bug = self.repo.get(bug_id)
        ensure_can_modify_bug(actor, bug.get("assignee_id"), bug["created_by"])
        if "status" in fields and fields["status"] is not None:
            target_status = BugStatus(str(fields["status"])).value
            self._ensure_transition(bug["status"], target_status)
            fields["status"] = target_status
        valid = {
            key: value
            for key, value in fields.items()
            if key in {"title", "description", "status", "severity", "assignee_id"}
            and value is not None
        }
        status_from = bug["status"]
        assignee_from = bug.get("assignee_id")
        changed_fields = [key for key, value in valid.items() if bug.get(key) != value]
        self.repo.update(bug_id, valid)
        if "status" in valid and bug["status"] != valid["status"]:
            self._append_activity(
                "BUG_STATUS_CHANGED",
                bug,
                actor.user_id,
                {
                    "row_id": bug_id,
                    "from": status_from,
                    "to": valid["status"],
                },
            )
        if "assignee_id" in valid and assignee_from != valid["assignee_id"]:
            self._append_activity(
                "BUG_ASSIGNED",
                bug,
                actor.user_id,
                {
                    "row_id": bug_id,
                    "from": assignee_from,
                    "to": valid["assignee_id"],
                },
            )
        edit_fields = sorted(field for field in changed_fields if field not in {"status", "assignee_id"})
        if edit_fields:
            self._append_activity(
                "BUG_EDITED",
                bug,
                actor.user_id,
                {
                    "row_id": bug_id,
                    "changed_fields": edit_fields,
                },
            )
        if changed_fields:
            event_type = (
                "status_changed"
                if "status" in changed_fields
                else "assigned"
                if "assignee_id" in changed_fields
                else "updated"
            )
            self._publish(event_type, self.repo.get(bug_id))

    def assign_bug(self, actor: Actor, bug_id: int, assignee_id: str | None) -> None:
        self.update_bug(actor, bug_id, assignee_id=assignee_id)

    def transition_status(self, actor: Actor, bug_id: int, status: str) -> None:
        self.update_bug(actor, bug_id, status=BugStatus(status).value)

    def resolve_bug(self, actor: Actor, bug_id: int) -> None:
        self.transition_status(actor, bug_id, BugStatus.RESOLVED.value)

    def reopen_bug(self, actor: Actor, bug_id: int) -> None:
        self.transition_status(actor, bug_id, BugStatus.OPEN.value)

    def _append_activity(self, event_type: str, bug: dict, actor_id: str, metadata: dict) -> None:
        if not self.activity_repo:
            return
        try:
            self.activity_repo.append(
                event_type=event_type,
                entity_type="bug",
                entity_id=bug_code(bug),
                actor_id=actor_id,
                metadata=metadata,
            )
        except Exception:  # pragma: no cover - defensive logging path
            logger.exception("Failed to append bug activity", extra={"event_type": event_type, "bug_id": bug["id"]})

    def _publish(self, event_type: str, bug: dict) -> None:
        if self.publication_hook is None:
            return
        try:
            self.publication_hook(event_type, bug)
        except Exception:  # pragma: no cover - defensive isolation boundary
            logger.exception(
                "Failed to schedule bug Forum publication",
                extra={"event_type": event_type, "bug_id": bug["id"]},
            )
