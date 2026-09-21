
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

from cse_hq_bot.ai.action_models import ActionProposal, ActionProposalDraft
from cse_hq_bot.errors import (
    AIActionConflictError,
    AIActionUnsupportedError,
    AIActionValidationError,
)
from cse_hq_bot.identifiers import bug_code, decision_code, meeting_code, task_code
from cse_hq_bot.models import Actor, BugStatus, MeetingStatus, TaskStatus
from cse_hq_bot.permissions import (
    ensure_can_manage_decisions,
    ensure_can_manage_meetings,
    ensure_can_modify_bug,
    ensure_can_modify_task,
)
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.standup_service import StandupService
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
        "meeting_create",
        "meeting_start",
        "meeting_complete",
        "meeting_cancel",
        "meeting_add_participant",
        "meeting_add_note",
        "decision_create",
        "decision_edit",
        "standup_submit",
        "standup_update",
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
MEETING_TRANSITIONS = {
    "meeting_start": MeetingStatus.IN_PROGRESS.value,
    "meeting_complete": MeetingStatus.COMPLETED.value,
    "meeting_cancel": MeetingStatus.CANCELLED.value,
}


class AIActionRegistry:
    def __init__(
        self,
        task_service: TaskService,
        bug_service: BugService,
        meeting_service: MeetingService | None = None,
        decision_service: DecisionService | None = None,
        standup_service: StandupService | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.task_service = task_service
        self.bug_service = bug_service
        self.meeting_service = meeting_service
        self.decision_service = decision_service
        self.standup_service = standup_service
        self.clock = clock or (lambda: datetime.now().astimezone())

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
        if action_type == "meeting_create":
            return self._prepare_meeting_create(actor, arguments)
        if action_type == "decision_create":
            return self._prepare_decision_create(actor, arguments)
        if action_type in {"standup_submit", "standup_update"}:
            return self._prepare_standup_action(actor, action_type, arguments)
        if target_id is None:
            raise AIActionValidationError("This action requires a target")
        if action_type.startswith("task_"):
            return self._prepare_task_action(actor, action_type, target_id, arguments)
        if action_type.startswith("bug_"):
            return self._prepare_bug_action(actor, action_type, target_id, arguments)
        if action_type.startswith("meeting_"):
            return self._prepare_meeting_action(actor, action_type, target_id, arguments)
        return self._prepare_decision_edit(actor, target_id, arguments)

    def execute(self, proposal: ActionProposal, actor: Actor) -> str:
        if proposal.action_type not in SUPPORTED_ACTIONS:
            raise AIActionUnsupportedError(
                f"Unsupported AI action: {proposal.action_type}"
            )
        if proposal.action_type == "task_create":
            return self._execute_task_create(proposal, actor)
        if proposal.action_type == "meeting_create":
            return self._execute_meeting_create(proposal, actor)
        if proposal.action_type == "decision_create":
            return self._execute_decision_create(proposal, actor)
        if proposal.action_type in {"standup_submit", "standup_update"}:
            return self._execute_standup_action(proposal, actor)
        if proposal.target_id is None:
            raise AIActionValidationError("This proposal has no target")
        if proposal.action_type.startswith("task_"):
            return self._execute_task_action(proposal, actor)
        if proposal.action_type.startswith("bug_"):
            return self._execute_bug_action(proposal, actor)
        if proposal.action_type.startswith("meeting_"):
            return self._execute_meeting_action(proposal, actor)
        return self._execute_decision_edit(proposal, actor)

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

    def _prepare_meeting_create(
        self, actor: Actor, arguments: dict[str, object]
    ) -> ActionProposalDraft:
        meeting_service = self._require_meeting_service()
        ensure_can_manage_meetings(actor)
        self._require_allowed_arguments(
            arguments,
            allowed={"title", "description", "agenda", "scheduled_at"},
            required={"title", "scheduled_at"},
        )
        title = self._clean_text(arguments.get("title"), "title", max_length=120)
        description = self._clean_optional_text(
            arguments.get("description"), max_length=2000
        )
        agenda = self._clean_optional_text(
            arguments.get("agenda"), max_length=2000
        )
        scheduled_at = self._normalize_scheduled_at(arguments.get("scheduled_at"))
        meeting_service._normalize_datetime(scheduled_at)
        return ActionProposalDraft(
            action_type="meeting_create",
            target_type=None,
            target_id=None,
            arguments={
                "title": title,
                "description": description,
                "agenda": agenda,
                "scheduled_at": scheduled_at,
            },
            summary=(
                f'Create meeting "{title}"\nScheduled: {scheduled_at}\n'
                f'Description: {description or "—"}\nAgenda: {agenda or "—"}'
            ),
        )

    def _prepare_meeting_action(
        self,
        actor: Actor,
        action_type: str,
        target_id: int,
        arguments: dict[str, object],
    ) -> ActionProposalDraft:
        meeting_service = self._require_meeting_service()
        meeting = meeting_service.get_meeting(actor, target_id)
        code = meeting_code(meeting)
        expected_state: dict[str, object] = {"status": meeting["status"]}
        if action_type in MEETING_TRANSITIONS:
            ensure_can_manage_meetings(actor)
            self._require_allowed_arguments(arguments, allowed=set(), required=set())
            target_status = MEETING_TRANSITIONS[action_type]
            if target_status not in meeting_service._TRANSITIONS.get(
                meeting["status"], set()
            ):
                raise AIActionValidationError(
                    f'Invalid meeting status transition: {meeting["status"]} -> {target_status}'
                )
            normalized = {}
            summary = (
                f'{code} "{meeting["title"]}": '
                f'{meeting["status"]} → {target_status}'
            )
        elif action_type == "meeting_add_participant":
            ensure_can_manage_meetings(actor)
            self._require_allowed_arguments(
                arguments,
                allowed={"participant_id"},
                required={"participant_id"},
            )
            participant_id = self._clean_text(
                arguments.get("participant_id"), "participant_id", max_length=64
            )
            participants = meeting_service.list_participants(actor, target_id)
            participant_ids = sorted(str(item["user_id"]) for item in participants)
            if participant_id in participant_ids:
                raise AIActionValidationError(
                    "That member is already a meeting participant"
                )
            expected_state["participant_ids"] = participant_ids
            normalized = {"participant_id": participant_id}
            summary = (
                f'Add participant {participant_id} to {code} "{meeting["title"]}"'
            )
        else:
            self._require_allowed_arguments(
                arguments, allowed={"content"}, required={"content"}
            )
            content = self._clean_text(
                arguments.get("content"), "content", max_length=2000
            )
            meeting_service._ensure_can_add_note(actor, target_id, meeting)
            normalized = {"content": content}
            summary = f'Add note to {code} "{meeting["title"]}"\nNew note: {content}'
        return ActionProposalDraft(
            action_type=action_type,
            target_type="meeting",
            target_id=target_id,
            arguments=normalized,
            summary=summary,
            expected_state=expected_state,
        )

    def _prepare_decision_create(
        self, actor: Actor, arguments: dict[str, object]
    ) -> ActionProposalDraft:
        self._require_decision_service()
        ensure_can_manage_decisions(actor)
        self._require_allowed_arguments(
            arguments,
            allowed={
                "title",
                "decision",
                "context",
                "rationale",
                "alternatives",
                "meeting_id",
            },
            required={"title", "decision"},
        )
        title = self._clean_text(arguments.get("title"), "title", max_length=160)
        decision = self._clean_text(
            arguments.get("decision"), "decision", max_length=4000
        )
        normalized: dict[str, object] = {
            "title": title,
            "decision": decision,
            "context": self._clean_optional_text(
                arguments.get("context"), max_length=4000
            ),
            "rationale": self._clean_optional_text(
                arguments.get("rationale"), max_length=4000
            ),
            "alternatives": self._clean_optional_text(
                arguments.get("alternatives"), max_length=4000
            ),
        }
        meeting_id = self._optional_positive_int(arguments.get("meeting_id"))
        if meeting_id is not None:
            self._require_meeting_service().get_meeting(actor, meeting_id)
            normalized["meeting_id"] = meeting_id
        meeting_label = f"\nMeeting: MEETING-{meeting_id:03d}" if meeting_id else ""
        return ActionProposalDraft(
            action_type="decision_create",
            target_type=None,
            target_id=None,
            arguments=normalized,
            summary=(
                f'Record decision "{title}"\nDecision: {decision}'
                f'\nRationale: {normalized["rationale"] or "—"}{meeting_label}'
            ),
        )

    def _prepare_decision_edit(
        self,
        actor: Actor,
        target_id: int,
        arguments: dict[str, object],
    ) -> ActionProposalDraft:
        decision_service = self._require_decision_service()
        ensure_can_manage_decisions(actor)
        decision = decision_service.get_decision(actor, target_id)
        allowed = {"title", "decision", "context", "rationale", "alternatives"}
        self._require_allowed_arguments(arguments, allowed=allowed, required=set())
        if not arguments:
            raise AIActionValidationError("Decision edit requires at least one field")
        normalized: dict[str, object] = {}
        for field, value in arguments.items():
            if field in {"title", "decision"}:
                normalized[field] = self._clean_text(
                    value, field, max_length=4000 if field == "decision" else 160
                )
            else:
                normalized[field] = self._clean_optional_text(value, max_length=4000)
        changed = [
            field
            for field, value in normalized.items()
            if decision.get(field) != value
        ]
        if not changed:
            raise AIActionValidationError("The decision already has those values")
        preview = "\n".join(
            f'{field.title()}:\nOLD: {decision.get(field) or "—"}\nNEW: {normalized[field] or "—"}'
            for field in changed
        )
        expected_state = {
            field: decision.get(field)
            for field in ("title", "decision", "context", "rationale", "alternatives")
        }
        return ActionProposalDraft(
            action_type="decision_edit",
            target_type="decision",
            target_id=target_id,
            arguments=normalized,
            summary=f'{decision_code(decision)} — {decision["title"]}\n{preview}',
            expected_state=expected_state,
        )

    def _prepare_standup_action(
        self,
        actor: Actor,
        action_type: str,
        arguments: dict[str, object],
    ) -> ActionProposalDraft:
        standup_service = self._require_standup_service()
        allowed = {"previous", "current", "blockers"}
        required = {"previous", "current"} if action_type == "standup_submit" else set()
        self._require_allowed_arguments(arguments, allowed=allowed, required=required)
        entry_date = self._today()
        existing = standup_service.get_today(actor, entry_date)
        if action_type == "standup_submit" and existing is not None:
            raise AIActionValidationError(
                "Today's standup already exists; update it instead"
            )
        if action_type == "standup_update" and existing is None:
            raise AIActionValidationError(
                "No standup exists for today; submit one first"
            )
        if action_type == "standup_update" and not arguments:
            raise AIActionValidationError("Standup update requires at least one field")
        normalized: dict[str, object] = {"entry_date": entry_date}
        for field, value in arguments.items():
            if field in {"previous", "current"}:
                normalized[field] = self._clean_text(
                    value, field, max_length=4000
                )
            else:
                normalized[field] = self._clean_optional_text(value, max_length=4000)
        if action_type == "standup_submit":
            normalized.setdefault("blockers", "")
            summary = (
                f"Submit standup for {entry_date}\n"
                f'Previous: {normalized["previous"]}\n'
                f'Current: {normalized["current"]}\n'
                f'Blockers: {normalized["blockers"] or "None"}'
            )
            expected_state = {"standup_absent": True, "entry_date": entry_date}
            target_id = None
        else:
            changed = [
                field
                for field, value in normalized.items()
                if field != "entry_date" and existing.get(field) != value
            ]
            if not changed:
                raise AIActionValidationError("Today's standup already has those values")
            preview = "\n".join(
                f'{field.title()}:\nOLD: {existing.get(field) or "None"}\nNEW: {normalized[field] or "None"}'
                for field in changed
            )
            summary = f"Update standup for {entry_date}\n{preview}"
            expected_state = {
                "id": existing["id"],
                "user_id": actor.user_id,
                "date": entry_date,
                "previous": existing.get("previous"),
                "current": existing.get("current"),
                "blockers": existing.get("blockers"),
            }
            target_id = int(existing["id"])
        return ActionProposalDraft(
            action_type=action_type,
            target_type="standup",
            target_id=target_id,
            arguments=normalized,
            summary=summary,
            expected_state=expected_state,
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

    def _execute_meeting_create(
        self, proposal: ActionProposal, actor: Actor
    ) -> str:
        prepared = self._prepare_meeting_create(actor, proposal.arguments)
        args = prepared.arguments
        meeting_service = self._require_meeting_service()
        meeting_id = meeting_service.create_meeting(
            actor,
            str(args["title"]),
            str(args.get("description") or ""),
            str(args.get("agenda") or ""),
            str(args["scheduled_at"]),
        )
        meeting = meeting_service.get_meeting(actor, meeting_id)
        return f'✅ Created {meeting_code(meeting)} "{meeting["title"]}" successfully.'

    def _execute_meeting_action(
        self, proposal: ActionProposal, actor: Actor
    ) -> str:
        meeting_service = self._require_meeting_service()
        meeting_id = int(proposal.target_id or 0)
        meeting = meeting_service.get_meeting(actor, meeting_id)
        current = dict(meeting)
        if proposal.action_type == "meeting_add_participant":
            current["participant_ids"] = sorted(
                str(item["user_id"])
                for item in meeting_service.list_participants(actor, meeting_id)
            )
        self._ensure_fresh(proposal, current)
        action = proposal.action_type
        if action == "meeting_start":
            meeting_service.start_meeting(actor, meeting_id)
        elif action == "meeting_complete":
            meeting_service.complete_meeting(actor, meeting_id)
        elif action == "meeting_cancel":
            meeting_service.cancel_meeting(actor, meeting_id)
        elif action == "meeting_add_participant":
            meeting_service.add_participant(
                actor, meeting_id, str(proposal.arguments["participant_id"])
            )
        elif action == "meeting_add_note":
            meeting_service.add_note(
                actor, meeting_id, str(proposal.arguments["content"])
            )
        updated = meeting_service.get_meeting(actor, meeting_id)
        return f'✅ {meeting_code(updated)} action completed successfully.'

    def _execute_decision_create(
        self, proposal: ActionProposal, actor: Actor
    ) -> str:
        prepared = self._prepare_decision_create(actor, proposal.arguments)
        args = prepared.arguments
        decision_service = self._require_decision_service()
        decision_id = decision_service.create_decision(
            actor,
            str(args["title"]),
            str(args["decision"]),
            str(args.get("context") or ""),
            str(args.get("rationale") or ""),
            str(args.get("alternatives") or ""),
            meeting_id=(
                int(args["meeting_id"])
                if args.get("meeting_id") is not None
                else None
            ),
        )
        decision = decision_service.get_decision(actor, decision_id)
        return f'✅ Recorded {decision_code(decision)} "{decision["title"]}" successfully.'

    def _execute_decision_edit(
        self, proposal: ActionProposal, actor: Actor
    ) -> str:
        decision_service = self._require_decision_service()
        decision_id = int(proposal.target_id or 0)
        decision = decision_service.get_decision(actor, decision_id)
        self._ensure_fresh(proposal, decision)
        decision_service.edit_decision(actor, decision_id, **proposal.arguments)
        updated = decision_service.get_decision(actor, decision_id)
        return f'✅ Updated {decision_code(updated)} "{updated["title"]}" successfully.'

    def _execute_standup_action(
        self, proposal: ActionProposal, actor: Actor
    ) -> str:
        standup_service = self._require_standup_service()
        entry_date = str(proposal.arguments.get("entry_date") or "")
        if entry_date != self._today():
            raise AIActionConflictError(
                "The standup date changed after this action was proposed"
            )
        existing = standup_service.get_today(actor, entry_date)
        if proposal.action_type == "standup_submit":
            if existing is not None:
                raise AIActionConflictError(
                    "Today's standup changed after this action was proposed"
                )
            previous = str(proposal.arguments["previous"])
            current = str(proposal.arguments["current"])
            blockers = str(proposal.arguments.get("blockers") or "")
        else:
            if existing is None:
                raise AIActionConflictError(
                    "Today's standup changed after this action was proposed"
                )
            self._ensure_fresh(proposal, existing)
            previous = str(proposal.arguments.get("previous", existing["previous"]))
            current = str(proposal.arguments.get("current", existing["current"]))
            blockers = str(proposal.arguments.get("blockers", existing["blockers"]))
        standup_id = standup_service.submit_standup(
            actor,
            previous,
            current,
            blockers,
            entry_date,
            allow_empty_previous=True,
        )
        action = "submitted" if proposal.action_type == "standup_submit" else "updated"
        return f"✅ Standup #{standup_id} for {entry_date} {action} successfully."

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

    def _optional_positive_int(self, value: object) -> int | None:
        if value in {None, ""}:
            return None
        stable_id = re.fullmatch(r"MEETING-(\d+)", str(value).strip(), re.IGNORECASE)
        if stable_id:
            return int(stable_id.group(1))
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise AIActionValidationError("Action meeting_id must be a number") from exc
        if number <= 0:
            raise AIActionValidationError("Action meeting_id must be positive")
        return number

    def _normalize_scheduled_at(self, value: object) -> str:
        text = self._clean_text(value, "scheduled_at", max_length=120)
        lowered = text.lower()
        relative = re.fullmatch(
            r"(today|tomorrow)(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
            lowered,
        )
        if relative:
            day_name, hour_text, minute_text, meridiem = relative.groups()
            hour = int(hour_text)
            minute = int(minute_text or 0)
            if meridiem:
                if not 1 <= hour <= 12:
                    raise AIActionValidationError("Use a valid meeting time")
                hour = hour % 12 + (12 if meridiem == "pm" else 0)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise AIActionValidationError("Use a valid meeting time")
            target_date = self.clock().date() + timedelta(
                days=1 if day_name == "tomorrow" else 0
            )
            return f"{target_date.isoformat()} {hour:02d}:{minute:02d}"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                return date.fromisoformat(text).isoformat() + " 00:00"
            except ValueError as exc:
                raise AIActionValidationError(
                    "Meeting time must resolve to an absolute ISO date and time"
                ) from exc
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AIActionValidationError(
                "Meeting time must resolve to an absolute ISO date and time"
            ) from exc
        return parsed.isoformat(sep=" ", timespec="minutes")

    def _today(self) -> str:
        return self.clock().date().isoformat()

    def _require_meeting_service(self) -> MeetingService:
        if self.meeting_service is None:
            raise AIActionUnsupportedError("Meeting AI actions are not configured")
        return self.meeting_service

    def _require_decision_service(self) -> DecisionService:
        if self.decision_service is None:
            raise AIActionUnsupportedError("Decision AI actions are not configured")
        return self.decision_service

    def _require_standup_service(self) -> StandupService:
        if self.standup_service is None:
            raise AIActionUnsupportedError("Standup AI actions are not configured")
        return self.standup_service
