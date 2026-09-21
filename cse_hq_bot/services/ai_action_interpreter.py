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
from cse_hq_bot.identifiers import bug_code, decision_code, meeting_code, task_code
from cse_hq_bot.models import Actor, BugStatus
from cse_hq_bot.services.ai_action_registry import AIActionRegistry
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService

INFORMATIONAL_PATTERN = re.compile(
    r"^\s*(?:how\b|what\b|who\b|why\b|when\b|where\b|is\b|are\b|"
    r"does\b|did\b|explain\b|can\s+(?!you\b|we\b))",
    re.IGNORECASE,
)
TASK_ID_PATTERN = re.compile(r"\bTASK-(\d+)\b", re.IGNORECASE)
BUG_ID_PATTERN = re.compile(r"\bBUG-(\d+)\b", re.IGNORECASE)
MEETING_ID_PATTERN = re.compile(r"\bMEETING-(\d+)\b", re.IGNORECASE)
DECISION_ID_PATTERN = re.compile(r"\bDEC-(\d+)\b", re.IGNORECASE)
ANY_TARGET_PATTERN = re.compile(
    r"\b(?:TASK|BUG|MEETING|DEC)-(\d+)\b", re.IGNORECASE
)
ACTION_VERB_PATTERN = re.compile(
    r"\b(create|add|assign|reassign|start|begin|block|complete|finish|"
    r"reopen|resolve|transition|move|set|change|merge|close|comment|rerun|"
    r"edit|update|delete|submit|schedule|record|cancel)\b",
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
                    "AI Actions v2.1 supports only bounded, confirmed internal mutations. "
                    "Deletion, another user's standup, GitHub, repository, and other writes remain unavailable."
                ),
            )

        verbs = ACTION_VERB_PATTERN.findall(normalized)
        targets = ANY_TARGET_PATTERN.findall(normalized)
        if len(targets) > 1 or self._has_multiple_actions(lowered, verbs):
            return ActionIntent(
                ActionIntentKind.MULTI_ACTION,
                message=(
                    "AI Actions v2.1 supports one project mutation at a time. "
                    "Choose the action you want to propose first."
                ),
            )

        is_task = bool(TASK_ID_PATTERN.search(normalized) or re.search(r"\btasks?\b", lowered))
        is_bug = bool(BUG_ID_PATTERN.search(normalized) or re.search(r"\bbugs?\b", lowered))
        is_meeting = bool(
            MEETING_ID_PATTERN.search(normalized)
            or re.search(r"\bmeetings?\b", lowered)
        )
        is_decision = bool(
            DECISION_ID_PATTERN.search(normalized)
            or re.search(r"\bdecisions?\b|\bdecid(?:e|ed|ing)\b", lowered)
        )
        is_standup = bool(
            re.search(r"\bstandups?\b|\bblockers?\b", lowered)
        )
        if is_meeting:
            if re.search(r"\b(?:create|schedule)\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "meeting_create")
            if re.search(r"\badd\b.*\bnotes?\b|\bnotes?\b.*\badd\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "meeting_add_note")
            if re.search(r"\badd\b.+\bto\b", lowered):
                return ActionIntent(
                    ActionIntentKind.SUPPORTED, "meeting_add_participant"
                )
            for pattern, action_type in (
                (r"\b(?:start|begin)\b", "meeting_start"),
                (r"\b(?:complete|finish)\b", "meeting_complete"),
                (r"\bcancel\b", "meeting_cancel"),
            ):
                if re.search(pattern, lowered):
                    return ActionIntent(ActionIntentKind.SUPPORTED, action_type)
        if is_decision:
            if re.search(r"\b(?:record|create|add)\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "decision_create")
            if re.search(r"\b(?:edit|update|change|set)\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "decision_edit")
        if is_standup:
            if re.search(r"\bsubmit\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "standup_submit")
            if re.search(r"\b(?:update|edit|change|set)\b", lowered):
                return ActionIntent(ActionIntentKind.SUPPORTED, "standup_update")
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
        if verbs and (is_task or is_bug or is_meeting or is_decision or is_standup):
            return ActionIntent(
                ActionIntentKind.UNSUPPORTED,
                message="That mutation is not supported by AI Actions v2.1.",
            )
        return ActionIntent(ActionIntentKind.NONE)

    def _is_unsupported_write(self, lowered: str) -> bool:
        mutation = bool(ACTION_VERB_PATTERN.search(lowered))
        external_target = bool(
            re.search(
                r"\b(?:gh-(?:issue|pr)-?\d*|github|pull request|pr\s*#?\d+|"
                r"issues?\s*#?\d+|branches?|"
                r"repository|repo|files?)\b",
                lowered,
            )
            or re.search(
                r"\b(?:edit|change|delete)\s+(?:the\s+)?code\b", lowered
            )
        )
        destructive_internal = bool(
            re.search(
                r"\bdelete\s+(?:the\s+)?(?:meetings?|decisions?|standups?)\b",
                lowered,
            )
        )
        other_standup = bool(
            re.search(r"\b(?:update|edit|change|set)\b", lowered)
            and (
                re.search(r"\b(?!my\b)[a-z0-9_-]+'s\s+standup\b", lowered)
                or re.search(r"\bstandup\s+for\s+(?!me\b|myself\b)", lowered)
            )
        )
        return (
            (mutation and external_target)
            or destructive_internal
            or other_standup
        )

    def _has_multiple_actions(self, lowered: str, verbs: list[str]) -> bool:
        return len(verbs) > 1 and " and " in lowered


class AIActionInterpreter:
    def __init__(
        self,
        provider: AIProvider,
        registry: AIActionRegistry,
        task_service: TaskService,
        bug_service: BugService,
        meeting_service: MeetingService | None = None,
        decision_service: DecisionService | None = None,
        standup_service: StandupService | None = None,
        *,
        timeout_seconds: int,
        max_candidates: int = 12,
    ):
        self.provider = provider
        self.registry = registry
        self.task_service = task_service
        self.bug_service = bug_service
        self.meeting_service = meeting_service
        self.decision_service = decision_service
        self.standup_service = standup_service
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
        if action_type in {
            "task_create",
            "meeting_create",
            "decision_create",
            "standup_submit",
            "standup_update",
        }:
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
        if action_type in {"task_assign", "bug_assign", "meeting_add_participant"}:
            name_key = (
                "participant_name"
                if action_type == "meeting_add_participant"
                else "assignee_name"
            )
            id_key = (
                "participant_id"
                if action_type == "meeting_add_participant"
                else "assignee_id"
            )
            query = arguments.pop(name_key, None) or arguments.get(id_key)
            if query is None:
                raise AIActionValidationError("Which project member did you mean?")
            arguments[id_key] = self._resolve_member(str(query), known_members)
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
        if action_type.startswith("task_"):
            pattern = TASK_ID_PATTERN
            target_kind = "task"
        elif action_type.startswith("bug_"):
            pattern = BUG_ID_PATTERN
            target_kind = "bug"
        elif action_type.startswith("meeting_"):
            pattern = MEETING_ID_PATTERN
            target_kind = "meeting"
        else:
            pattern = DECISION_ID_PATTERN
            target_kind = "decision"
        explicit = pattern.search(question)
        requested = payload.get("target_id")
        if explicit:
            explicit_id = int(explicit.group(1))
            if requested is not None:
                requested_id = self._parse_target_id(requested, target_kind)
                if requested_id != explicit_id:
                    raise AIActionValidationError(
                        "The AI changed the explicitly requested target"
                    )
            try:
                if target_kind == "task":
                    self.task_service.get_task(actor, explicit_id)
                elif target_kind == "bug":
                    self.bug_service.get_bug(actor, explicit_id)
                elif target_kind == "meeting":
                    self._require_meeting_service().get_meeting(actor, explicit_id)
                else:
                    self._require_decision_service().get_decision(actor, explicit_id)
            except (NotFoundError, PermissionDeniedError) as exc:
                raise AIActionValidationError(
                    "The requested target is unavailable or inaccessible"
                ) from exc
            return explicit_id

        query = str(payload.get("target_query") or "").strip()
        if not query:
            raise AIActionValidationError("Which project record did you mean?")
        if target_kind == "task":
            candidates = self.task_service.list_accessible_tasks(actor)
        elif target_kind == "bug":
            candidates = self.bug_service.list_accessible_bugs(actor)
        elif target_kind == "meeting":
            candidates = self._require_meeting_service().list_accessible_meetings(actor)
        else:
            candidates = self._require_decision_service().list_accessible_decisions(actor)
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
                f'{self._target_code(target_kind, item)} — {item["title"]}'
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

        if action_type == "meeting_create":
            match = re.search(
                r"\b(?:create|schedule)(?:\s+(?:a|new))?\s+(.+?)\s+meeting\s+(.+)$",
                question,
                re.IGNORECASE,
            )
            if not match:
                raise AIActionValidationError(
                    "A meeting title and resolved time are required"
                )
            payload["arguments"] = {
                "title": match.group(1).strip(' "'),
                "scheduled_at": re.sub(
                    r"^(?:for\s+)?", "", match.group(2).strip(" ."), flags=re.IGNORECASE
                ),
                "description": "",
                "agenda": "",
            }
            return payload

        if action_type == "decision_create":
            match = re.search(
                r"\brecord(?:\s+(?:a|the))?(?:\s+decision)?\s+that\s+(.+)$",
                question,
                re.IGNORECASE | re.DOTALL,
            )
            if not match:
                raise AIActionValidationError("Decision text is required")
            statement = match.group(1).strip(" .")
            parts = re.split(r"\s+because\s+", statement, maxsplit=1, flags=re.IGNORECASE)
            decision_text = parts[0].strip(" .")
            payload["arguments"] = {
                "title": decision_text[:160],
                "decision": decision_text,
                "context": "",
                "rationale": parts[1].strip(" .") if len(parts) == 2 else "",
                "alternatives": "",
            }
            return payload

        if action_type == "standup_submit":
            previous = re.search(
                r"\byesterday\s+(?:i\s+)?(.+?)(?=[,;\n]\s*today\b)",
                question,
                re.IGNORECASE | re.DOTALL,
            )
            current = re.search(
                r"\btoday\s+(?:i(?:'m|\s+am)?\s+)?(.+?)"
                r"(?=[,;\n]\s*(?:blocked\s+by|blockers?\s*:)|$)",
                question,
                re.IGNORECASE | re.DOTALL,
            )
            blockers = re.search(
                r"\b(?:blocked\s+by|blockers?\s*:)\s*(.+)$",
                question,
                re.IGNORECASE | re.DOTALL,
            )
            if not previous or not current:
                raise AIActionValidationError(
                    "Standup previous and current updates are required"
                )
            blocker_text = blockers.group(1).strip(" .") if blockers else ""
            if blocker_text.lower() in {"none", "no blockers", "nothing"}:
                blocker_text = ""
            payload["arguments"] = {
                "previous": previous.group(1).strip(" ."),
                "current": current.group(1).strip(" ."),
                "blockers": blocker_text,
            }
            return payload

        if action_type == "standup_update":
            match = re.search(
                r"\b(?:update|change|set)\s+(?:my\s+)?"
                r"(blockers?|previous|current)\s+(?:to\s+)?(.+)$",
                question,
                re.IGNORECASE | re.DOTALL,
            )
            if not match:
                raise AIActionValidationError("Which standup field should change?")
            field = match.group(1).lower()
            field = "blockers" if field.startswith("blocker") else field
            value = match.group(2).strip(" .")
            if field == "blockers" and value.lower() in {
                "none",
                "no blockers",
                "nothing",
            }:
                value = ""
            payload["arguments"] = {field: value}
            return payload

        if action_type.startswith("task_"):
            id_pattern, prefix = TASK_ID_PATTERN, "TASK"
        elif action_type.startswith("bug_"):
            id_pattern, prefix = BUG_ID_PATTERN, "BUG"
        elif action_type.startswith("meeting_"):
            id_pattern, prefix = MEETING_ID_PATTERN, "MEETING"
        else:
            id_pattern, prefix = DECISION_ID_PATTERN, "DEC"
        target_match = id_pattern.search(question)
        if target_match:
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
        elif action_type == "meeting_add_participant":
            match = re.search(
                r"\badd\s+(.+?)\s+to\s+(?:the\s+)?(?:MEETING-\d+|.+?meeting)\b",
                question,
                re.IGNORECASE,
            )
            if not match:
                raise AIActionValidationError("Which participant should be added?")
            arguments["participant_name"] = match.group(1).strip(" .")
        elif action_type == "meeting_add_note":
            match = re.search(r"\bthat\s+(.+)$", question, re.IGNORECASE | re.DOTALL)
            if not match:
                raise AIActionValidationError("Meeting note content is required")
            arguments["content"] = match.group(1).strip(" .")
        elif action_type == "decision_edit":
            match = re.search(
                r"\b(title|decision|context|rationale|alternatives)\b\s+(?:to\s+)(.+)$",
                question,
                re.IGNORECASE | re.DOTALL,
            )
            if not match:
                raise AIActionValidationError(
                    "Specify the decision field and its new value"
                )
            arguments[match.group(1).lower()] = match.group(2).strip(" .")
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
        if action_type == "meeting_add_participant":
            match = re.search(
                r"\bto\s+(?:the\s+)?(.+?)(?:\s+meeting)?\s*$",
                question,
                re.IGNORECASE,
            )
            return match.group(1).strip(" .") if match else ""
        if action_type == "meeting_add_note":
            match = re.search(
                r"\bto\s+(?:the\s+)?(.+?)(?:\s+meeting)?\s+that\b",
                question,
                re.IGNORECASE,
            )
            return match.group(1).strip(" .") if match else ""
        text = re.sub(
            r"^\s*(?:can\s+you\s+|please\s+)?"
            r"(?:assign|reassign|start|begin|block|complete|finish|reopen|resolve|"
            r"transition|move|set|change|cancel|edit|update)\s+",
            "",
            question,
            flags=re.IGNORECASE,
        )
        if action_type in {"task_assign", "bug_assign"}:
            text = re.split(r"\s+to\s+", text, maxsplit=1, flags=re.IGNORECASE)[0]
        text = re.sub(
            r"\b(?:the|this|a|an|task|bug|meeting|decision)\b",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        return " ".join(text.strip(" .").split())

    def _parse_target_id(self, value: object, target_kind: str) -> int:
        patterns = {
            "task": TASK_ID_PATTERN,
            "bug": BUG_ID_PATTERN,
            "meeting": MEETING_ID_PATTERN,
            "decision": DECISION_ID_PATTERN,
        }
        pattern = patterns[target_kind]
        match = pattern.fullmatch(str(value).strip())
        if not match:
            raise AIActionValidationError("The AI returned an invalid target ID")
        return int(match.group(1))

    def _normalize_name(self, value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())

    def _target_code(self, target_kind: str, item: dict) -> str:
        if target_kind == "task":
            return task_code(item)
        if target_kind == "bug":
            return bug_code(item)
        if target_kind == "meeting":
            return meeting_code(item)
        return decision_code(item)

    def _require_meeting_service(self) -> MeetingService:
        if self.meeting_service is None:
            raise AIActionValidationError("Meeting AI actions are not configured")
        return self.meeting_service

    def _require_decision_service(self) -> DecisionService:
        if self.decision_service is None:
            raise AIActionValidationError("Decision AI actions are not configured")
        return self.decision_service

    def _require_standup_service(self) -> StandupService:
        if self.standup_service is None:
            raise AIActionValidationError("Standup AI actions are not configured")
        return self.standup_service

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
        elif action_type.startswith("bug_"):
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
        elif action_type.startswith("meeting_"):
            for meeting in self._require_meeting_service().list_accessible_meetings(actor)[
                : self.max_candidates
            ]:
                records.append(
                    RetrievedContextRecord(
                        "meeting",
                        meeting_code(meeting),
                        str(meeting["title"]),
                        (
                            f'Status: {meeting["status"]}; '
                            f'scheduled: {meeting.get("scheduled_at")}'
                        ),
                        meeting.get("updated_at"),
                        "AI action target candidate",
                    )
                )
        elif action_type.startswith("decision_"):
            if action_type == "decision_create":
                for meeting in self._require_meeting_service().list_accessible_meetings(
                    actor
                )[: self.max_candidates]:
                    records.append(
                        RetrievedContextRecord(
                            "meeting",
                            meeting_code(meeting),
                            str(meeting["title"]),
                            (
                                f'Status: {meeting["status"]}; '
                                f'scheduled: {meeting.get("scheduled_at")}'
                            ),
                            meeting.get("updated_at"),
                            "Optional decision meeting candidate",
                        )
                    )
            else:
                for decision in self._require_decision_service().list_accessible_decisions(
                    actor
                )[: self.max_candidates]:
                    records.append(
                        RetrievedContextRecord(
                            "decision",
                            decision_code(decision),
                            str(decision["title"]),
                            str(decision["decision"]),
                            decision.get("updated_at"),
                            "AI action target candidate",
                        )
                    )
        else:
            for standup in self._require_standup_service().list_accessible_standups(
                actor
            )[: self.max_candidates]:
                records.append(
                    RetrievedContextRecord(
                        "standup",
                        str(standup.get("code") or f'STANDUP-{standup["id"]:03d}'),
                        f'{standup["user_id"]} — {standup["date"]}',
                        (
                            f'Previous: {standup.get("previous")}; '
                            f'Current: {standup.get("current")}; '
                            f'Blockers: {standup.get("blockers")}'
                        ),
                        standup.get("updated_at"),
                        "Current actor standup candidate",
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
