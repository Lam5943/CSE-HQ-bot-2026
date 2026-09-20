
from cse_hq_bot.ai.action_models import ActionProposal, ActionProposalDraft
from cse_hq_bot.errors import (
    AIActionConflictError,
    AIActionUnsupportedError,
    AIActionValidationError,
)
from cse_hq_bot.identifiers import bug_code, task_code
from cse_hq_bot.models import Actor, BugStatus, TaskStatus
from cse_hq_bot.permissions import ensure_can_modify_bug, ensure_can_modify_task
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.task_service import TaskService

SUPPORTED_ACTIONS = frozenset(
    {
        "task_create",
        "task_assign",
        "task_start",
        "task_block",
        "task_complete",
        "task_reopen",
        "bug_assign",
        "bug_transition",
        "bug_resolve",
        "bug_reopen",
    }
)

TASK_TRANSITIONS = {
    "task_start": TaskStatus.IN_PROGRESS.value,
    "task_block": TaskStatus.BLOCKED.value,
    "task_complete": TaskStatus.DONE.value,
    "task_reopen": TaskStatus.TODO.value,
}
BUG_TRANSITIONS = {
    "bug_resolve": BugStatus.RESOLVED.value,
    "bug_reopen": BugStatus.OPEN.value,
}


class AIActionRegistry:
    def __init__(self, task_service: TaskService, bug_service: BugService):
        self.task_service = task_service
        self.bug_service = bug_service

    def prepare(
        self,
        *,
        actor: Actor,
        action_type: str,
        target_id: int | None,
        arguments: dict[str, object],
    ) -> ActionProposalDraft:
        if action_type not in SUPPORTED_ACTIONS:
            raise AIActionUnsupportedError(f"Unsupported AI action: {action_type}")
        if action_type == "task_create":
            return self._prepare_task_create(arguments)
        if target_id is None:
            raise AIActionValidationError("This action requires a target")
        if action_type.startswith("task_"):
            return self._prepare_task_action(actor, action_type, target_id, arguments)
        return self._prepare_bug_action(actor, action_type, target_id, arguments)

    def execute(self, proposal: ActionProposal, actor: Actor) -> str:
        if proposal.action_type not in SUPPORTED_ACTIONS:
            raise AIActionUnsupportedError(
                f"Unsupported AI action: {proposal.action_type}"
            )
        if proposal.action_type == "task_create":
            return self._execute_task_create(proposal, actor)
        if proposal.target_id is None:
            raise AIActionValidationError("This proposal has no target")
        if proposal.action_type.startswith("task_"):
            return self._execute_task_action(proposal, actor)
        return self._execute_bug_action(proposal, actor)

    def _prepare_task_create(
        self, arguments: dict[str, object]
    ) -> ActionProposalDraft:
        self._require_allowed_arguments(
            arguments,
            allowed={"title", "description", "priority", "assignee_id", "deadline"},
            required={"title"},
        )
        title = self._clean_text(arguments.get("title"), "title", max_length=120)
        description = self._clean_optional_text(
            arguments.get("description"), max_length=2000
        )
        priority = self._bounded_int(arguments.get("priority", 3), "priority", 1, 5)
        normalized = {
            "title": title,
            "description": description,
            "priority": priority,
        }
        for key in ("assignee_id", "deadline"):
            value = arguments.get(key)
            if value is not None and str(value).strip():
                normalized[key] = str(value).strip()
        return ActionProposalDraft(
            action_type="task_create",
            target_type=None,
            target_id=None,
            arguments=normalized,
            summary=f'Create task "{title}" with priority {priority}',
        )

    def _prepare_task_action(
        self,
        actor: Actor,
        action_type: str,
        target_id: int,
        arguments: dict[str, object],
    ) -> ActionProposalDraft:
        task = self.task_service.get_task(actor, target_id)
        ensure_can_modify_task(actor, task.get("assignee_id"), task["created_by"])
        code = task_code(task)
        if action_type == "task_assign":
            self._require_allowed_arguments(
                arguments, allowed={"assignee_id"}, required={"assignee_id"}
            )
            assignee_id = self._clean_text(
                arguments.get("assignee_id"), "assignee_id", max_length=64
            )
            return ActionProposalDraft(
                action_type=action_type,
                target_type="task",
                target_id=target_id,
                arguments={"assignee_id": assignee_id},
                summary=(
                    f'Assign {code} "{task["title"]}" from '
                    f'{task.get("assignee_id") or "unassigned"} to {assignee_id}'
                ),
                expected_state={"assignee_id": task.get("assignee_id")},
            )
        self._require_allowed_arguments(arguments, allowed=set(), required=set())
        target_status = TASK_TRANSITIONS[action_type]
        self.task_service._ensure_transition(task["status"], target_status)
        return ActionProposalDraft(
            action_type=action_type,
            target_type="task",
            target_id=target_id,
            arguments={},
            summary=(
                f'{code} "{task["title"]}": '
                f'{task["status"]} → {target_status}'
            ),
            expected_state={"status": task["status"]},
        )

    def _prepare_bug_action(
        self,
        actor: Actor,
        action_type: str,
        target_id: int,
        arguments: dict[str, object],
    ) -> ActionProposalDraft:
        bug = self.bug_service.get_bug(actor, target_id)
        ensure_can_modify_bug(actor, bug.get("assignee_id"), bug["created_by"])
        code = bug_code(bug)
        if action_type == "bug_assign":
            self._require_allowed_arguments(
                arguments, allowed={"assignee_id"}, required={"assignee_id"}
            )
            assignee_id = self._clean_text(
                arguments.get("assignee_id"), "assignee_id", max_length=64
            )
            return ActionProposalDraft(
                action_type=action_type,
                target_type="bug",
                target_id=target_id,
                arguments={"assignee_id": assignee_id},
                summary=(
                    f'Assign {code} "{bug["title"]}" from '
                    f'{bug.get("assignee_id") or "unassigned"} to {assignee_id}'
                ),
                expected_state={"assignee_id": bug.get("assignee_id")},
            )
        if action_type == "bug_transition":
            self._require_allowed_arguments(
                arguments, allowed={"to_status"}, required={"to_status"}
            )
            try:
                target_status = BugStatus(str(arguments["to_status"]).lower()).value
            except ValueError as exc:
                raise AIActionValidationError("Unsupported bug status") from exc
            normalized_arguments = {"to_status": target_status}
        else:
            self._require_allowed_arguments(arguments, allowed=set(), required=set())
            target_status = BUG_TRANSITIONS[action_type]
            normalized_arguments = {}
        self.bug_service._ensure_transition(bug["status"], target_status)
        return ActionProposalDraft(
            action_type=action_type,
            target_type="bug",
            target_id=target_id,
            arguments=normalized_arguments,
            summary=(
                f'{code} "{bug["title"]}": '
                f'{bug["status"]} → {target_status}'
            ),
            expected_state={"status": bug["status"]},
        )

    def _execute_task_create(self, proposal: ActionProposal, actor: Actor) -> str:
        prepared = self._prepare_task_create(proposal.arguments)
        args = prepared.arguments
        task_id = self.task_service.create_task(
            actor,
            str(args["title"]),
            str(args.get("description") or ""),
            int(args["priority"]),
            assignee_id=self._optional_string(args.get("assignee_id")),
            deadline=self._optional_string(args.get("deadline")),
        )
        task = self.task_service.get_task(actor, task_id)
        return f'✅ Created {task_code(task)} "{task["title"]}" successfully.'

    def _execute_task_action(self, proposal: ActionProposal, actor: Actor) -> str:
        task_id = int(proposal.target_id or 0)
        task = self.task_service.get_task(actor, task_id)
        self._ensure_fresh(proposal, task)
        action = proposal.action_type
        if action == "task_assign":
            self.task_service.assign_task(
                actor, task_id, str(proposal.arguments["assignee_id"])
            )
        elif action == "task_start":
            self.task_service.start_task(actor, task_id)
        elif action == "task_block":
            self.task_service.block_task(actor, task_id)
        elif action == "task_complete":
            self.task_service.complete_task(actor, task_id)
        elif action == "task_reopen":
            self.task_service.reopen_task(actor, task_id)
        updated = self.task_service.get_task(actor, task_id)
        return f'✅ {task_code(updated)} action completed successfully.'

    def _execute_bug_action(self, proposal: ActionProposal, actor: Actor) -> str:
        bug_id = int(proposal.target_id or 0)
        bug = self.bug_service.get_bug(actor, bug_id)
        self._ensure_fresh(proposal, bug)
        action = proposal.action_type
        if action == "bug_assign":
            self.bug_service.assign_bug(
                actor, bug_id, str(proposal.arguments["assignee_id"])
            )
        elif action == "bug_transition":
            self.bug_service.transition_status(
                actor, bug_id, str(proposal.arguments["to_status"])
            )
        elif action == "bug_resolve":
            self.bug_service.resolve_bug(actor, bug_id)
        elif action == "bug_reopen":
            self.bug_service.reopen_bug(actor, bug_id)
        updated = self.bug_service.get_bug(actor, bug_id)
        return f'✅ {bug_code(updated)} action completed successfully.'

    def _ensure_fresh(self, proposal: ActionProposal, current: dict) -> None:
        for key, expected in proposal.expected_state.items():
            if current.get(key) != expected:
                raise AIActionConflictError(
                    "The target changed after this action was proposed"
                )

    def _require_allowed_arguments(
        self,
        arguments: dict[str, object],
        *,
        allowed: set[str],
        required: set[str],
    ) -> None:
        unknown = set(arguments) - allowed
        missing = {key for key in required if arguments.get(key) in {None, ""}}
        if unknown:
            raise AIActionValidationError(
                f"Unsupported action arguments: {', '.join(sorted(unknown))}"
            )
        if missing:
            raise AIActionValidationError(
                f"Missing required action arguments: {', '.join(sorted(missing))}"
            )

    def _clean_text(self, value: object, field: str, *, max_length: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise AIActionValidationError(f"Action {field} is required")
        if len(text) > max_length:
            raise AIActionValidationError(f"Action {field} is too long")
        return text

    def _clean_optional_text(self, value: object, *, max_length: int) -> str:
        text = str(value or "").strip()
        if len(text) > max_length:
            raise AIActionValidationError("Action description is too long")
        return text

    def _bounded_int(
        self, value: object, field: str, minimum: int, maximum: int
    ) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise AIActionValidationError(f"Action {field} must be a number") from exc
        if not minimum <= number <= maximum:
            raise AIActionValidationError(
                f"Action {field} must be between {minimum} and {maximum}"
            )
        return number

    def _optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
