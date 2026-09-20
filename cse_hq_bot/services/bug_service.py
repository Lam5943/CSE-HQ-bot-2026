from cse_hq_bot.models import Actor, BugStatus
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

    def update_bug(self, actor: Actor, bug_id: int, **fields: object) -> None:
        bug = self.repo.get(bug_id)
        ensure_can_modify_bug(actor, bug.get("assignee_id"), bug["created_by"])
        if "status" in fields and fields["status"] is not None:
            fields["status"] = BugStatus(str(fields["status"])).value
        valid = {
            key: value
            for key, value in fields.items()
            if key in {"title", "description", "status", "severity", "assignee_id"}
            and value is not None
        }
        self.repo.update(bug_id, valid)

    def resolve_bug(self, actor: Actor, bug_id: int) -> None:
        self.update_bug(actor, bug_id, status=BugStatus.RESOLVED.value)
