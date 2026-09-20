import json
import re

from cse_hq_bot.ai.action_models import (
    ActionIntent,
    ActionIntentKind,
    ActionProposalDraft,
    KnownMember,
)
from cse_hq_bot.ai.base import AIMessage, AIProvider, RetrievedContextRecord
from cse_hq_bot.errors import (
    AIActionValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from cse_hq_bot.identifiers import bug_code, task_code
from cse_hq_bot.models import Actor, BugStatus
from cse_hq_bot.services.ai_action_registry import AIActionRegistry
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.task_service import TaskService

INFORMATIONAL_PATTERN = re.compile(
    r"^\s*(?:how\b|what\b|who\b|why\b|when\b|where\b|is\b|are\b|"
    r"does\b|did\b|explain\b|can\s+(?!you\b|we\b))",
    re.IGNORECASE,
)
TASK_ID_PATTERN = re.compile(r"\bTASK-(\d+)\b", re.IGNORECASE)
BUG_ID_PATTERN = re.compile(r"\bBUG-(\d+)\b", re.IGNORECASE)
ANY_TARGET_PATTERN = re.compile(r"\b(?:TASK|BUG)-(\d+)\b", re.IGNORECASE)
ACTION_VERB_PATTERN = re.compile(
    r"\b(create|add|assign|reassign|start|begin|block|complete|finish|"
    r"reopen|resolve|transition|move|set|change|merge|close|comment|rerun|"
    r"edit|delete|submit|schedule)\b",
    re.IGNORECASE,
)


class ActionIntentDetector:
    def detect(self, question: str) -> ActionIntent:
        normalized = " ".join(question.strip().split())
        lowered = normalized.lower()
        if INFORMATIONAL_PATTERN.search(normalized):
            return ActionIntent(ActionIntentKind.NONE)

        if self._is_unsupported_write(lowered):
            return ActionIntent(
                ActionIntentKind.UNSUPPORTED,
                message=(
                    "AI Actions v2 supports only one confirmed Task or Bug mutation at a time. "
                    "GitHub, meeting, decision, standup, repository, and other writes remain unavailable."
                ),
            )

        verbs = ACTION_VERB_PATTERN.findall(normalized)
        targets = ANY_TARGET_PATTERN.findall(normalized)
        if len(targets) > 1 or self._has_multiple_actions(lowered, verbs):
            return ActionIntent(
                ActionIntentKind.MULTI_ACTION,
                message=(
                    "AI Actions v2 supports one project mutation at a time. "
                    "Choose the Task or Bug action you want to propose first."
                ),
            )

        is_task = bool(TASK_ID_PATTERN.search(normalized) or re.search(r"\btasks?\b", lowered))
        is_bug = bool(BUG_ID_PATTERN.search(normalized) or re.search(r"\bbugs?\b", lowered))
        if re.search(r"\b(?:create|add)\b.*\btasks?\b", lowered):
            return ActionIntent(ActionIntentKind.SUPPORTED, "task_create")
        if re.search(r"\b(?:assign|reassign)\b", lowered):
            if is_task:
                return ActionIntent(ActionIntentKind.SUPPORTED, "task_assign")
            if is_bug:
                return ActionIntent(ActionIntentKind.SUPPORTED, "bug_assign")
        if is_task:
            for pattern, action_type in (
                (r"\b(?:start|begin)\b", "task_start"),
                (r"\bblock\b", "task_block"),
                (r"\b(?:complete|finish)\b|\bmark\b.*\bdone\b", "task_complete"),
                (r"\breopen\b", "task_reopen"),
            ):
                if re.search(pattern, lowered):
                    return ActionIntent(ActionIntentKind.SUPPORTED, action_type)
        if is_bug:
            if re.search(r"\breopen\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "bug_reopen")
            if re.search(r"\bresolve\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "bug_resolve")
            if re.search(
                r"\b(?:transition|move|set|change|triage|investigate)\b", lowered
            ):
                return ActionIntent(ActionIntentKind.SUPPORTED, "bug_transition")
        if verbs and (is_task or is_bug):
            return ActionIntent(
                ActionIntentKind.UNSUPPORTED,
                message="That Task or Bug mutation is not supported by AI Actions v2.",
            )
        return ActionIntent(ActionIntentKind.NONE)

    def _is_unsupported_write(self, lowered: str) -> bool:
        mutation = bool(ACTION_VERB_PATTERN.search(lowered))
        unsupported_target = bool(
            re.search(
                r"\b(?:gh-(?:issue|pr)-?\d*|github|pull request|pr\s*#?\d+|"
                r"issues?\s*#?\d+|meetings?|decisions?|standups?|branches?|"
                r"repository|repo|files?|code)\b",
                lowered,
            )
        )
        return mutation and unsupported_target

    def _has_multiple_actions(self, lowered: str, verbs: list[str]) -> bool:
        normalized_verbs = {
            "create" if verb.lower() == "add" else verb.lower() for verb in verbs
        }
        return len(normalized_verbs) > 1 and " and " in lowered


class AIActionInterpreter:
    def __init__(
        self,
        provider: AIProvider,
        registry: AIActionRegistry,
        task_service: TaskService,
        bug_service: BugService,
        *,
        timeout_seconds: int,
        max_candidates: int = 12,
    ):
        self.provider = provider
        self.registry = registry
        self.task_service = task_service
        self.bug_service = bug_service
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_candidates = max(1, int(max_candidates))

    async def interpret(
        self,
        *,
        actor: Actor,
        question: str,
        action_type: str,
        history_messages: list[dict],
        known_members: list[KnownMember],
    ) -> ActionProposalDraft:
        try:
            raw = self._deterministic_payload(question, action_type)
        except AIActionValidationError:
            if self.provider.__class__.__name__ == "FakeAIProvider":
                raise
            context = self._action_context(actor, action_type, known_members)
            response = await self.provider.generate(
                system_instruction=self._action_system_instruction(action_type),
                messages=[
                    AIMessage(role=item["role"], content=item["content"])
                    for item in history_messages
                    if item.get("role") in {"user", "assistant"}
                ]
                + [AIMessage(role="user", content=question)],
                context_records=context,
                timeout_seconds=self.timeout_seconds,
            )
            raw = self.parse_structured_output(
                response.text, expected_action_type=action_type
            )
        return self._resolve_and_prepare(
            actor=actor,
            question=question,
            action_type=action_type,
            payload=raw,
            known_members=known_members,
        )

    def parse_structured_output(
        self, text: str, *, expected_action_type: str
    ) -> dict[str, object]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            payload = json.loads(cleaned)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AIActionValidationError(
                "The AI returned an invalid action proposal"
            ) from exc
        if not isinstance(payload, dict):
            raise AIActionValidationError("The AI action proposal must be an object")
        allowed = {"kind", "action_type", "target_id", "target_query", "arguments"}
        if set(payload) - allowed:
            raise AIActionValidationError("The AI action proposal has unknown fields")
        if payload.get("kind") != "action_proposal":
            raise AIActionValidationError("The AI did not return an action proposal")
        if payload.get("action_type") != expected_action_type:
            raise AIActionValidationError("The AI changed the requested action type")
        arguments = payload.get("arguments", {})
        if not isinstance(arguments, dict):
            raise AIActionValidationError("Action arguments must be an object")
        return dict(payload)

    def _resolve_and_prepare(
        self,
        *,
        actor: Actor,
        question: str,
        action_type: str,
        payload: dict[str, object],
        known_members: list[KnownMember],
    ) -> ActionProposalDraft:
        arguments = dict(payload.get("arguments") or {})
        if action_type == "task_create":
            if arguments.get("assignee_name") is not None:
                arguments["assignee_id"] = self._resolve_member(
                    str(arguments.pop("assignee_name")), known_members
                )
            elif arguments.get("assignee_id") is not None:
                arguments["assignee_id"] = self._resolve_member(
                    str(arguments["assignee_id"]), known_members
                )
            return self.registry.prepare(
                actor=actor,
                action_type=action_type,
                target_id=None,
                arguments=arguments,
            )

        target_id = self._resolve_target(
            actor=actor,
            action_type=action_type,
            question=question,
            payload=payload,
        )
        if action_type in {"task_assign", "bug_assign"}:
            query = arguments.pop("assignee_name", None) or arguments.get(
                "assignee_id"
            )
            if query is None:
                raise AIActionValidationError("Who should receive this assignment?")
            arguments["assignee_id"] = self._resolve_member(
                str(query), known_members
            )
        return self.registry.prepare(
            actor=actor,
            action_type=action_type,
            target_id=target_id,
            arguments=arguments,
        )

    def _resolve_target(
        self,
        *,
        actor: Actor,
        action_type: str,
        question: str,
        payload: dict[str, object],
    ) -> int:
        is_task = action_type.startswith("task_")
        pattern = TASK_ID_PATTERN if is_task else BUG_ID_PATTERN
        explicit = pattern.search(question)
        requested = payload.get("target_id")
        if explicit:
            explicit_id = int(explicit.group(1))
            if requested is not None:
                requested_id = self._parse_target_id(requested, is_task)
                if requested_id != explicit_id:
                    raise AIActionValidationError(
                        "The AI changed the explicitly requested target"
                    )
            try:
                if is_task:
                    self.task_service.get_task(actor, explicit_id)
                else:
                    self.bug_service.get_bug(actor, explicit_id)
            except (NotFoundError, PermissionDeniedError) as exc:
                raise AIActionValidationError(
                    "The requested target is unavailable or inaccessible"
                ) from exc
            return explicit_id

        query = str(payload.get("target_query") or "").strip()
        if not query:
            raise AIActionValidationError("Which project record did you mean?")
        candidates = (
            self.task_service.list_accessible_tasks(actor)
            if is_task
            else self.bug_service.list_accessible_bugs(actor)
        )
        normalized = self._normalize_name(query)
        exact = [
            item
            for item in candidates
            if self._normalize_name(str(item["title"])) == normalized
        ]
        matches = exact or [
            item
            for item in candidates
            if normalized in self._normalize_name(str(item["title"]))
        ]
        if len(matches) == 1:
            return int(matches[0]["id"])
        if len(matches) > 1:
            labels = [
                f'{task_code(item) if is_task else bug_code(item)} — {item["title"]}'
                for item in matches[:5]
            ]
            raise AIActionValidationError(
                "Which record did you mean?\n" + "\n".join(labels)
            )
        raise AIActionValidationError("No accessible project record matched that target")

    def _resolve_member(
        self, query: str, known_members: list[KnownMember]
    ) -> str:
        mention = re.fullmatch(r"<@!?(\d+)>", query.strip())
        normalized = self._normalize_name(query)
        matches = [
            member
            for member in known_members
            if (mention and member.user_id == mention.group(1))
            or member.user_id == query.strip()
            or normalized
            in {
                self._normalize_name(member.display_name),
                self._normalize_name(member.username or ""),
            }
        ]
        unique = {member.user_id: member for member in matches}
        if len(unique) == 1:
            return next(iter(unique))
        if len(unique) > 1:
            labels = ", ".join(
                f"{member.display_name} ({member.user_id})"
                for member in unique.values()
            )
            raise AIActionValidationError(f"That member name is ambiguous: {labels}")
        raise AIActionValidationError("That assignee is not a known project member")

    def _deterministic_payload(
        self, question: str, action_type: str
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": "action_proposal",
            "action_type": action_type,
            "arguments": {},
        }
        if action_type == "task_create":
            match = re.search(
                r"\b(?:create|add)(?:\s+(?:a|new))?\s+task"
                r"(?:\s+(?:called|titled))?\s+(.+)$",
                question,
                re.IGNORECASE,
            )
            if not match:
                raise AIActionValidationError("A task title is required")
            remainder = match.group(1).strip(" .")
            priority_match = re.search(r"\bpriority\s*[:=]?\s*([1-5])\b", remainder, re.IGNORECASE)
            description_match = re.search(
                r"\b(?:with\s+)?description\s*[:=]?\s*(.+?)(?=\s*,?\s*priority\b|$)",
                remainder,
                re.IGNORECASE,
            )
            title = re.split(
                r"\s*,?\s*(?:with\s+)?description\b|\s*,?\s*priority\b",
                remainder,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(' "')
            arguments: dict[str, object] = {
                "title": title,
                "description": description_match.group(1).strip(" .")
                if description_match
                else "",
                "priority": int(priority_match.group(1)) if priority_match else 3,
            }
            payload["arguments"] = arguments
            return payload

        id_pattern = TASK_ID_PATTERN if action_type.startswith("task_") else BUG_ID_PATTERN
        target_match = id_pattern.search(question)
        if target_match:
            prefix = "TASK" if action_type.startswith("task_") else "BUG"
            payload["target_id"] = f"{prefix}-{int(target_match.group(1)):03d}"
        else:
            target_query = self._extract_target_query(question, action_type)
            if not target_query:
                raise AIActionValidationError("Which project record did you mean?")
            payload["target_query"] = target_query

        arguments = {}
        if action_type in {"task_assign", "bug_assign"}:
            match = re.search(r"\bto\s+(.+)$", question, re.IGNORECASE)
            if not match:
                raise AIActionValidationError("Who should receive this assignment?")
            arguments["assignee_name"] = match.group(1).strip(" .")
        elif action_type == "bug_transition":
            lowered = question.lower()
            status = None
            for candidate in BugStatus:
                if candidate.value.replace("_", " ") in lowered:
                    status = candidate.value
                    break
            if status is None and "triage" in lowered:
                status = BugStatus.TRIAGED.value
            if status is None and "investigat" in lowered:
                status = BugStatus.IN_PROGRESS.value
            if status is None:
                raise AIActionValidationError("Which supported bug status should be used?")
            arguments["to_status"] = status
        payload["arguments"] = arguments
        return payload

    def _extract_target_query(self, question: str, action_type: str) -> str:
        quoted = re.search(r'["“](.+?)["”]', question)
        if quoted:
            return quoted.group(1).strip()
        text = re.sub(
            r"^\s*(?:can\s+you\s+|please\s+)?"
            r"(?:assign|reassign|start|begin|block|complete|finish|reopen|resolve|"
            r"transition|move|set|change)\s+",
            "",
            question,
            flags=re.IGNORECASE,
        )
        if action_type in {"task_assign", "bug_assign"}:
            text = re.split(r"\s+to\s+", text, maxsplit=1, flags=re.IGNORECASE)[0]
        text = re.sub(r"\b(?:the|this|a|an|task|bug)\b", " ", text, flags=re.IGNORECASE)
        return " ".join(text.strip(" .").split())

    def _parse_target_id(self, value: object, is_task: bool) -> int:
        pattern = TASK_ID_PATTERN if is_task else BUG_ID_PATTERN
        match = pattern.fullmatch(str(value).strip())
        if not match:
            raise AIActionValidationError("The AI returned an invalid target ID")
        return int(match.group(1))

    def _normalize_name(self, value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())

    def _action_context(
        self,
        actor: Actor,
        action_type: str,
        known_members: list[KnownMember],
    ) -> list[RetrievedContextRecord]:
        records: list[RetrievedContextRecord] = []
        if action_type.startswith("task_"):
            for task in self.task_service.list_accessible_tasks(actor)[: self.max_candidates]:
                records.append(
                    RetrievedContextRecord(
                        "task",
                        task_code(task),
                        str(task["title"]),
                        f'Status: {task["status"]}; assignee: {task.get("assignee_id")}',
                        task.get("created_at"),
                        "AI action target candidate",
                    )
                )
        else:
            for bug in self.bug_service.list_accessible_bugs(actor)[: self.max_candidates]:
                records.append(
                    RetrievedContextRecord(
                        "bug",
                        bug_code(bug),
                        str(bug["title"]),
                        f'Status: {bug["status"]}; assignee: {bug.get("assignee_id")}',
                        bug.get("created_at"),
                        "AI action target candidate",
                    )
                )
        for member in known_members[: self.max_candidates]:
            records.append(
                RetrievedContextRecord(
                    "project_member",
                    f"MEMBER-{member.user_id}",
                    member.display_name,
                    f"User ID: {member.user_id}; username: {member.username or ''}",
                    None,
                    "AI action assignee candidate",
                )
            )
        return records

    def _action_system_instruction(self, action_type: str) -> str:
        return (
            "You only interpret one requested project mutation. You cannot execute actions and "
            "must never claim success. Return JSON only with keys kind, action_type, target_id "
            "or target_query, and arguments. kind must be action_proposal. action_type must be "
            f"{action_type}. Use only accessible candidates in PROJECT_CONTEXT. Do not invent IDs, "
            "users, function names, arguments, or additional actions. The application validates "
            "the proposal and requires explicit user confirmation before any mutation."
        )
