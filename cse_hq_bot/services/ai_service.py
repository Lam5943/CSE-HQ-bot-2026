import re
from dataclasses import dataclass

from cse_hq_bot.ai.base import AIProvider, RetrievedContextRecord
from cse_hq_bot.errors import PermissionDeniedError
from cse_hq_bot.identifiers import bug_code, decision_code, meeting_code, standup_code, task_code
from cse_hq_bot.models import Actor
from cse_hq_bot.services.project_context_service import ProjectContextService
from cse_hq_bot.services.prompt_builder import PromptBuilder
from cse_hq_bot.services.retrieval_planner import RetrievalPlan, RetrievalPlanner

SOURCE_ID_PATTERN = re.compile(r"\b(?:TASK|BUG|MEETING|DEC|STANDUP)-\d+\b")
MUTATION_PATTERN = re.compile(
    r"\b(?:complete|assign|update|change|create|delete|resolve|close|reopen|start|block|record|submit|schedule|cancel)\b",
    re.IGNORECASE,
)
PROJECT_MUTATION_TARGET_PATTERN = re.compile(
    r"\b(?:task|bug|meeting|decision|standup|project)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GroundedAnswer:
    content: str
    source_refs: list[str]
    invalid_source_refs: list[str]
    retrieval_strategy: str


class AIService:
    def __init__(
        self,
        provider: AIProvider,
        project_context_service: ProjectContextService,
        retrieval_planner: RetrievalPlanner,
        prompt_builder: PromptBuilder,
        *,
        max_context_items: int,
        request_timeout: int,
    ):
        self.provider = provider
        self.project_context_service = project_context_service
        self.retrieval_planner = retrieval_planner
        self.prompt_builder = prompt_builder
        self.max_context_items = max(1, int(max_context_items))
        self.request_timeout = max(1, int(request_timeout))

    async def answer_question(
        self,
        *,
        actor: Actor,
        question: str,
        history_messages: list[dict],
    ) -> GroundedAnswer:
        if self._is_mutation_request(question):
            return GroundedAnswer(
                content=(
                    "AI project mutations are unavailable in CSE-HQ v1. "
                    "I can help explain the current project state, but I cannot change tasks, bugs, meetings, decisions, or standups."
                ),
                source_refs=[],
                invalid_source_refs=[],
                retrieval_strategy="mutation_rejected",
            )
        plan = self.retrieval_planner.plan(question)
        context_records = self._retrieve_records(actor, plan)
        prompt = self.prompt_builder.build(
            history_messages=history_messages,
            user_question=question,
            context_records=context_records,
        )
        response = await self.provider.generate(
            system_instruction=prompt.system_instruction,
            messages=prompt.messages,
            context_records=prompt.context_records,
            timeout_seconds=self.request_timeout,
        )
        valid, invalid = self._validate_source_refs(response.text, context_records)
        return GroundedAnswer(
            content=response.text,
            source_refs=valid,
            invalid_source_refs=invalid,
            retrieval_strategy=plan.strategy,
        )

    def _is_mutation_request(self, question: str) -> bool:
        return bool(MUTATION_PATTERN.search(question) and PROJECT_MUTATION_TARGET_PATTERN.search(question))

    def _retrieve_records(self, actor: Actor, plan: RetrievalPlan) -> list[RetrievedContextRecord]:
        strategy = plan.strategy
        if strategy == "current_work":
            return self._records_from_current_work(self.project_context_service.get_current_work(actor), "current work")
        if strategy == "blockers":
            return self._records_from_blockers(self.project_context_service.get_blockers(actor), "blockers")
        if strategy == "overview":
            return self._records_from_overview(self.project_context_service.get_project_overview(actor), "project overview")
        if strategy == "recent_activity":
            return self._records_from_activity(
                self.project_context_service.get_activity_between(
                    actor,
                    plan.start_time or "",
                    plan.end_time or "",
                    limit=self.max_context_items,
                ),
                "recent activity",
            )
        if strategy == "meeting_context" and plan.meeting_id is not None:
            try:
                return self._records_from_meeting_context(
                    self.project_context_service.get_meeting_context(actor, plan.meeting_id),
                    "meeting context",
                )
            except PermissionDeniedError:
                raise
            except Exception:
                pass
        if strategy == "meeting_lookup":
            matches = self.project_context_service.search_project_memory(
                actor,
                plan.query,
                domains=list(plan.domains),
                limit=self.max_context_items,
            )
            if matches:
                first = matches[0]["source_id"]
                if first.startswith("MEETING-"):
                    meeting_id = int(first.split("-")[1])
                    return self._records_from_meeting_context(
                        self.project_context_service.get_meeting_context(actor, meeting_id),
                        "meeting lookup",
                    )
            return self._records_from_search_results(matches, "meeting lookup")
        if strategy == "decision_reasoning":
            decision_matches = self.project_context_service.search_decisions(actor, plan.query)[: self.max_context_items]
            memory_matches = self.project_context_service.search_project_memory(
                actor,
                plan.query,
                domains=list(plan.domains),
                limit=self.max_context_items,
            )
            return (
                self._records_from_decisions(decision_matches, "decision reasoning")
                + self._records_from_search_results(memory_matches, "decision reasoning")
            )[: self.max_context_items]
        matches = self.project_context_service.search_project_memory(actor, plan.query, limit=self.max_context_items)
        return self._records_from_search_results(matches, "project search")

    def _records_from_current_work(self, payload: dict, reason: str) -> list[RetrievedContextRecord]:
        records: list[RetrievedContextRecord] = []
        for task in payload.get("active_tasks", []):
            records.append(self._task_record(task, reason))
        for task in payload.get("blocked_tasks", []):
            records.append(self._task_record(task, reason))
        standup = payload.get("current_standup")
        if standup:
            records.append(self._standup_record(standup, reason))
        for bug in payload.get("open_bugs", []):
            records.append(self._bug_record(bug, reason))
        return records[: self.max_context_items]

    def _records_from_blockers(self, payload: dict, reason: str) -> list[RetrievedContextRecord]:
        records: list[RetrievedContextRecord] = []
        for task in payload.get("blocked_tasks", []):
            records.append(self._task_record(task, reason))
        for bug in payload.get("high_severity_bugs", []):
            records.append(self._bug_record(bug, reason))
        for standup in payload.get("standups_with_blockers", []):
            records.append(self._standup_record(standup, reason))
        return records[: self.max_context_items]

    def _records_from_overview(self, payload: dict, reason: str) -> list[RetrievedContextRecord]:
        project = payload.get("project", {})
        summary = (
            f"Description: {project.get('description', '')}\n"
            f"Goal: {project.get('goal', '')}\n"
            f"Phase: {project.get('phase', '')}\n"
            f"Sprint: {project.get('sprint', '')}\n"
            f"Deadline: {project.get('deadline', '')}\n"
            f"Status: {project.get('status', '')}\n"
            f"Task statistics: {payload.get('task_statistics', {})}\n"
            f"Bug statistics: {payload.get('bug_statistics', {})}\n"
            f"Standup summary: {payload.get('standup_summary', {})}"
        )
        records = [
            RetrievedContextRecord(
                source_type="project",
                source_id="PROJECT-OVERVIEW",
                title=project.get("name") or "Project Overview",
                content=summary,
                timestamp=project.get("updated_at"),
                retrieval_reason=reason,
            )
        ]
        for meeting in payload.get("active_meetings", []):
            records.append(self._meeting_record(meeting, reason))
        for decision in payload.get("recent_decisions", []):
            records.append(self._decision_record(decision, reason))
        return records[: self.max_context_items]

    def _records_from_activity(self, items: list[dict], reason: str) -> list[RetrievedContextRecord]:
        return [
            RetrievedContextRecord(
                source_type=item.get("entity_type") or "activity",
                source_id=item.get("entity_id") or f"ACTIVITY-{item['id']}",
                title=item.get("event_type") or "Activity",
                content=f"Actor: {item.get('actor_id')}\nMetadata: {item.get('metadata', {})}",
                timestamp=item.get("created_at"),
                retrieval_reason=reason,
            )
            for item in items[: self.max_context_items]
        ]

    def _records_from_meeting_context(self, payload: dict, reason: str) -> list[RetrievedContextRecord]:
        meeting = payload["meeting"]
        records = [self._meeting_record(meeting, reason)]
        notes = payload.get("notes", [])
        if notes:
            records.append(
                RetrievedContextRecord(
                    source_type="meeting_note",
                    source_id=meeting_code(meeting),
                    title=f"Notes for {meeting_code(meeting)}",
                    content="\n".join(f"{note['author_id']}: {note['content']}" for note in notes[:5]),
                    timestamp=notes[0].get("updated_at") or notes[0].get("created_at"),
                    retrieval_reason=reason,
                )
            )
        for decision in payload.get("decisions", []):
            records.append(self._decision_record(decision, reason))
        for task in payload.get("tasks", []):
            records.append(self._task_record(task, reason))
        return records[: self.max_context_items]

    def _records_from_search_results(self, results: list[dict], reason: str) -> list[RetrievedContextRecord]:
        return [
            RetrievedContextRecord(
                source_type=result["source_type"],
                source_id=result["source_id"],
                title=result["title"],
                content=result.get("snippet") or "",
                timestamp=result.get("timestamp"),
                retrieval_reason=reason,
            )
            for result in results[: self.max_context_items]
        ]

    def _records_from_decisions(self, decisions: list[dict], reason: str) -> list[RetrievedContextRecord]:
        return [self._decision_record(decision, reason) for decision in decisions[: self.max_context_items]]

    def _task_record(self, task: dict, reason: str) -> RetrievedContextRecord:
        return RetrievedContextRecord(
            source_type="task",
            source_id=task_code(task),
            title=task["title"],
            content=(
                f"Status: {task.get('status')}\nPriority: {task.get('priority')}\n"
                f"Assignee: {task.get('assignee_id')}\nDescription: {task.get('description') or ''}"
            ),
            timestamp=task.get("completed_at") or task.get("created_at"),
            retrieval_reason=reason,
        )

    def _bug_record(self, bug: dict, reason: str) -> RetrievedContextRecord:
        return RetrievedContextRecord(
            source_type="bug",
            source_id=bug_code(bug),
            title=bug["title"],
            content=(
                f"Status: {bug.get('status')}\nSeverity: {bug.get('severity')}\n"
                f"Assignee: {bug.get('assignee_id')}\nDescription: {bug.get('description') or ''}"
            ),
            timestamp=bug.get("resolved_at") or bug.get("created_at"),
            retrieval_reason=reason,
        )

    def _meeting_record(self, meeting: dict, reason: str) -> RetrievedContextRecord:
        return RetrievedContextRecord(
            source_type="meeting",
            source_id=meeting_code(meeting),
            title=meeting["title"],
            content=(
                f"Status: {meeting.get('status')}\nScheduled: {meeting.get('scheduled_at')}\n"
                f"Description: {meeting.get('description') or ''}\nAgenda: {meeting.get('agenda') or ''}"
            ),
            timestamp=meeting.get("updated_at") or meeting.get("created_at") or meeting.get("scheduled_at"),
            retrieval_reason=reason,
        )

    def _decision_record(self, decision: dict, reason: str) -> RetrievedContextRecord:
        return RetrievedContextRecord(
            source_type="decision",
            source_id=decision_code(decision),
            title=decision.get("title") or decision.get("decision") or "Untitled decision",
            content=(
                f"Decision: {decision.get('decision') or ''}\nContext: {decision.get('context') or ''}\n"
                f"Rationale: {decision.get('rationale') or ''}\nAlternatives: {decision.get('alternatives') or ''}"
            ),
            timestamp=decision.get("updated_at") or decision.get("created_at"),
            retrieval_reason=reason,
        )

    def _standup_record(self, standup: dict, reason: str) -> RetrievedContextRecord:
        return RetrievedContextRecord(
            source_type="standup",
            source_id=standup_code(standup),
            title=f"Standup for {standup['user_id']} on {standup['date']}",
            content=(
                f"Previous: {standup.get('previous') or ''}\nCurrent: {standup.get('current') or ''}\n"
                f"Blockers: {standup.get('blockers') or ''}"
            ),
            timestamp=standup.get("updated_at") or standup.get("created_at"),
            retrieval_reason=reason,
        )

    def _validate_source_refs(self, text: str, context_records: list[RetrievedContextRecord]) -> tuple[list[str], list[str]]:
        cited = list(dict.fromkeys(SOURCE_ID_PATTERN.findall(text)))
        allowed = {record.source_id for record in context_records}
        valid = [source_id for source_id in cited if source_id in allowed]
        invalid = [source_id for source_id in cited if source_id not in allowed]
        return valid, invalid
