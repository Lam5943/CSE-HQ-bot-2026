from cse_hq_bot.errors import CSEHQError, PermissionDeniedError
from cse_hq_bot.models import Actor, BugStatus, Role
from cse_hq_bot.permissions import ensure_can_modify_bug
from cse_hq_bot.repositories.bug_repository import BugRepository


class BugService:
    def __init__(self, repo: BugRepository):
        self.repo = repo

    def report_bug(
        self,
        actor: Actor,
        title: str,
        description: str,
        severity: int,
        assignee_id: str | None = None,
    ) -> int:
        return self.repo.create(
            title=title,
            description=description,
            severity=severity,
            created_by=actor.user_id,
            assignee_id=assignee_id,
        )

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
        transitions = {
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
                BugStatus.IN_PROGRESS.value,
            },
        }
        if target_status not in transitions.get(current_status, set()):
            raise CSEHQError(f"Invalid bug status transition: {current_status} -> {target_status}")

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
        self.repo.update(bug_id, valid)

    def assign_bug(self, actor: Actor, bug_id: int, assignee_id: str | None) -> None:
        self.update_bug(actor, bug_id, assignee_id=assignee_id)

    def transition_status(self, actor: Actor, bug_id: int, status: str) -> None:
        self.update_bug(actor, bug_id, status=BugStatus(status).value)

    def resolve_bug(self, actor: Actor, bug_id: int) -> None:
        self.transition_status(actor, bug_id, BugStatus.RESOLVED.value)

    def reopen_bug(self, actor: Actor, bug_id: int) -> None:
        self.transition_status(actor, bug_id, BugStatus.OPEN.value)
