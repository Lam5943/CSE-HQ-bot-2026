from cse_hq_bot.errors import CSEHQError, PermissionDeniedError
from cse_hq_bot.models import Actor, Role, TaskStatus
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

    def _can_access(self, actor: Actor, task: dict) -> bool:
        if actor.role in {Role.LEADER, Role.CO_LEAD}:
            return True
        return actor.user_id in {task.get("assignee_id"), task.get("created_by")}

    def list_accessible_tasks(self, actor: Actor) -> list[dict]:
        return [task for task in self.list_tasks() if self._can_access(actor, task)]

    def list_my_tasks(self, actor: Actor) -> list[dict]:
        return [task for task in self.list_accessible_tasks(actor) if task.get("assignee_id") == actor.user_id]

    def get_task(self, actor: Actor, task_id: int) -> dict:
        task = self.repo.get(task_id)
        if not self._can_access(actor, task):
            raise PermissionDeniedError("You are not allowed to view this task")
        return task

    def filter_tasks(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        priority: int | None = None,
        assignee_id: str | None = None,
        deadline: str | None = None,
        mine_only: bool = False,
        search: str | None = None,
    ) -> list[dict]:
        tasks = self.list_my_tasks(actor) if mine_only else self.list_accessible_tasks(actor)
        if status:
            status_value = TaskStatus(status).value
            tasks = [task for task in tasks if task["status"] == status_value]
        if priority is not None:
            tasks = [task for task in tasks if int(task["priority"]) == int(priority)]
        if assignee_id:
            tasks = [task for task in tasks if task.get("assignee_id") == assignee_id]
        if deadline:
            needle = deadline.strip().lower()
            tasks = [task for task in tasks if needle in (task.get("deadline") or "").lower()]
        if search:
            needle = search.strip().lower()
            tasks = [
                task
                for task in tasks
                if needle in task["title"].lower() or needle in task["description"].lower()
            ]
        return tasks

    def task_statistics(self, actor: Actor) -> dict:
        tasks = self.list_accessible_tasks(actor)
        status_counts = {status.value: 0 for status in TaskStatus}
        for task in tasks:
            status_counts[task["status"]] = status_counts.get(task["status"], 0) + 1
        return {
            "total": len(tasks),
            "my_tasks": len(self.list_my_tasks(actor)),
            "by_status": status_counts,
            "high_priority": len([task for task in tasks if int(task["priority"]) >= 4]),
            "with_deadline": len([task for task in tasks if task.get("deadline")]),
        }

    def _ensure_transition(self, current_status: str, target_status: str) -> None:
        transitions = {
            TaskStatus.TODO.value: {
                TaskStatus.IN_PROGRESS.value,
                TaskStatus.BLOCKED.value,
                TaskStatus.DONE.value,
            },
            TaskStatus.IN_PROGRESS.value: {
                TaskStatus.BLOCKED.value,
                TaskStatus.DONE.value,
                TaskStatus.TODO.value,
            },
            TaskStatus.BLOCKED.value: {
                TaskStatus.IN_PROGRESS.value,
                TaskStatus.TODO.value,
                TaskStatus.DONE.value,
            },
            TaskStatus.DONE.value: {
                TaskStatus.TODO.value,
                TaskStatus.IN_PROGRESS.value,
            },
        }
        if target_status not in transitions.get(current_status, set()):
            raise CSEHQError(f"Invalid task status transition: {current_status} -> {target_status}")

    def update_task(self, actor: Actor, task_id: int, **fields: object) -> None:
        task = self.repo.get(task_id)
        ensure_can_modify_task(actor, task.get("assignee_id"), task["created_by"])
        if "status" in fields and fields["status"] is not None:
            target_status = TaskStatus(str(fields["status"])).value
            self._ensure_transition(task["status"], target_status)
            fields["status"] = target_status
        valid = {
            key: value
            for key, value in fields.items()
            if key in {"title", "description", "status", "priority", "assignee_id", "deadline"}
            and value is not None
        }
        self.repo.update(task_id, valid)

    def assign_task(self, actor: Actor, task_id: int, assignee_id: str | None) -> None:
        self.update_task(actor, task_id, assignee_id=assignee_id)

    def transition_status(self, actor: Actor, task_id: int, status: str) -> None:
        self.update_task(actor, task_id, status=TaskStatus(status).value)

    def start_task(self, actor: Actor, task_id: int) -> None:
        self.transition_status(actor, task_id, TaskStatus.IN_PROGRESS.value)

    def block_task(self, actor: Actor, task_id: int) -> None:
        self.transition_status(actor, task_id, TaskStatus.BLOCKED.value)

    def reopen_task(self, actor: Actor, task_id: int) -> None:
        self.transition_status(actor, task_id, TaskStatus.TODO.value)

    def complete_task(self, actor: Actor, task_id: int) -> None:
        self.transition_status(actor, task_id, TaskStatus.DONE.value)
