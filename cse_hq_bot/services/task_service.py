import logging
from typing import ClassVar

from cse_hq_bot.errors import InvalidTransitionError, PermissionDeniedError
from cse_hq_bot.identifiers import task_code
from cse_hq_bot.models import Actor, Role, TaskStatus
from cse_hq_bot.permissions import ensure_can_modify_task
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.task_repository import TaskRepository

logger = logging.getLogger(__name__)


class TaskService:
    _TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        TaskStatus.TODO.value: {
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.BLOCKED.value,
        },
        TaskStatus.IN_PROGRESS.value: {
            TaskStatus.BLOCKED.value,
            TaskStatus.DONE.value,
            TaskStatus.TODO.value,
        },
        TaskStatus.BLOCKED.value: {
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.TODO.value,
        },
        TaskStatus.DONE.value: {
            TaskStatus.TODO.value,
        },
    }

    def __init__(self, repo: TaskRepository, activity_repo: ActivityRepository | None = None):
        self.repo = repo
        self.activity_repo = activity_repo

    def create_task(
        self,
        actor: Actor,
        title: str,
        description: str,
        priority: int,
        assignee_id: str | None = None,
        deadline: str | None = None,
        source_meeting_id: int | None = None,
    ) -> int:
        task_id = self.repo.create(
            title=title,
            description=description,
            priority=priority,
            created_by=actor.user_id,
            assignee_id=assignee_id,
            deadline=deadline,
            source_meeting_id=source_meeting_id,
        )
        task = self.repo.get(task_id)
        self._append_activity(
            "TASK_CREATED",
            task,
            actor.user_id,
            {
                "row_id": task_id,
                "status": task["status"],
                "assignee_id": task.get("assignee_id"),
                "source_meeting_id": task.get("source_meeting_id"),
            },
        )
        return task_id

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
        if target_status not in self._TRANSITIONS.get(current_status, set()):
            raise InvalidTransitionError(
                f"Invalid task status transition: {current_status} -> {target_status}"
            )

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
        status_from = task["status"]
        assignee_from = task.get("assignee_id")
        changed_fields = [key for key, value in valid.items() if task.get(key) != value]
        self.repo.update(task_id, valid)
        if "status" in valid and task["status"] != valid["status"]:
            self._append_activity(
                "TASK_STATUS_CHANGED",
                task,
                actor.user_id,
                {
                    "row_id": task_id,
                    "from": status_from,
                    "to": valid["status"],
                },
            )
        if "assignee_id" in valid and assignee_from != valid["assignee_id"]:
            self._append_activity(
                "TASK_ASSIGNED",
                task,
                actor.user_id,
                {
                    "row_id": task_id,
                    "from": assignee_from,
                    "to": valid["assignee_id"],
                },
            )
        edit_fields = sorted(
            field
            for field in changed_fields
            if field not in {"status", "assignee_id"}
        )
        if edit_fields:
            self._append_activity(
                "TASK_EDITED",
                task,
                actor.user_id,
                {
                    "row_id": task_id,
                    "changed_fields": edit_fields,
                },
            )

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

    def _append_activity(self, event_type: str, task: dict, actor_id: str, metadata: dict) -> None:
        if not self.activity_repo:
            return
        try:
            self.activity_repo.append(
                event_type=event_type,
                entity_type="task",
                entity_id=task_code(task),
                actor_id=actor_id,
                metadata=metadata,
            )
        except Exception:  # pragma: no cover - defensive logging path
            logger.exception("Failed to append task activity", extra={"event_type": event_type, "task_id": task["id"]})
