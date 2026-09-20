from cse_hq_bot.models import Actor, TaskStatus
from cse_hq_bot.permissions import ensure_can_modify_task
from cse_hq_bot.repositories.task_repository import TaskRepository


class TaskService:
    def __init__(self, repo: TaskRepository):
        self.repo = repo

    def create_task(
        self,
        actor: Actor,
        title: str,
        description: str,
        priority: int,
        assignee_id: str | None = None,
        deadline: str | None = None,
    ) -> int:
        return self.repo.create(
            title=title,
            description=description,
            priority=priority,
            created_by=actor.user_id,
            assignee_id=assignee_id,
            deadline=deadline,
        )

    def list_tasks(self) -> list[dict]:
        return self.repo.list_all()

    def update_task(self, actor: Actor, task_id: int, **fields: object) -> None:
        task = self.repo.get(task_id)
        ensure_can_modify_task(actor, task.get("assignee_id"), task["created_by"])
        if "status" in fields and fields["status"] is not None:
            fields["status"] = TaskStatus(str(fields["status"])).value
        valid = {
            key: value
            for key, value in fields.items()
            if key in {"title", "description", "status", "priority", "assignee_id", "deadline"}
            and value is not None
        }
        self.repo.update(task_id, valid)

    def complete_task(self, actor: Actor, task_id: int) -> None:
        self.update_task(actor, task_id, status=TaskStatus.DONE.value)
