import re
from dataclasses import dataclass

from cse_hq_bot.ai.action_models import (
    ActionIntentKind,
    ActionProposal,
    ActionProposalDraft,
    KnownMember,
)
from cse_hq_bot.ai.base import AIImage, AIProvider, RetrievedContextRecord
from cse_hq_bot.errors import (
    AIActionError,
    AIProviderError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
)
from cse_hq_bot.identifiers import (
    bug_code,
    decision_code,
    meeting_code,
    standup_code,
    task_code,
)
from cse_hq_bot.models import Actor
from cse_hq_bot.services.ai_action_interpreter import (
    ActionIntentDetector,
    AIActionInterpreter,
)
from cse_hq_bot.services.project_context_service import ProjectContextService
from cse_hq_bot.services.prompt_builder import PromptBuilder
from cse_hq_bot.services.retrieval_planner import RetrievalPlan, RetrievalPlanner
from cse_hq_bot.services.web_research_service import (
    TavilyWebResearchService,
    WebResearchIntentDetector,
)

SOURCE_ID_PATTERN = re.compile(
    r"\b(?:(?:TASK|BUG|MEETING|DEC|STANDUP|WEB)-\d+|GH-(?:ISSUE|PR)-\d+|GH-COMMIT-[0-9a-fA-F]{7,40})\b"
)
@dataclass(frozen=True)
class GroundedAnswer:
    content: str
    source_refs: list[str]
    invalid_source_refs: list[str]
    retrieval_strategy: str
    action_draft: ActionProposalDraft | None = None
    action_proposal: ActionProposal | None = None


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
        action_interpreter: AIActionInterpreter | None = None,
        action_intent_detector: ActionIntentDetector | None = None,
        web_research_service: TavilyWebResearchService | None = None,
        web_research_intent_detector: WebResearchIntentDetector | None = None,
    ):
        self.provider = provider
        self.project_context_service = project_context_service
        self.retrieval_planner = retrieval_planner
        self.prompt_builder = prompt_builder
        self.max_context_items = max(1, int(max_context_items))
        self.request_timeout = max(1, int(request_timeout))
        self.action_interpreter = action_interpreter
        self.action_intent_detector = action_intent_detector or ActionIntentDetector()
        self.web_research_service = web_research_service
        self.web_research_intent_detector = (
            web_research_intent_detector or WebResearchIntentDetector()
        )

    async def answer_question(
        self,
        *,
        actor: Actor,
        question: str,
        history_messages: list[dict],
        known_members: list[KnownMember] | None = None,
        images: list[AIImage] | None = None,
        allow_actions: bool = True,
    ) -> GroundedAnswer:
        images = images or []
        intent = self.action_intent_detector.detect(question)
        if intent.kind != ActionIntentKind.NONE and not allow_actions:
            return GroundedAnswer(
                content=(
                    "Public mentions are read-only. I can explain or research this, "
                    "but project changes need a private /ai session so CSE-HQ can show "
                    "a Confirm/Cancel proposal first."
                ),
                source_refs=[],
                invalid_source_refs=[],
                retrieval_strategy="public_mutation_rejected",
            )
        if intent.kind != ActionIntentKind.NONE and self.action_interpreter is None:
            return GroundedAnswer(
                content=(
                    "AI project mutations are unavailable in this configuration. "
                    "I can still answer questions about the current project state."
                ),
                source_refs=[],
                invalid_source_refs=[],
                retrieval_strategy="mutation_rejected",
            )
        if intent.kind in {ActionIntentKind.UNSUPPORTED, ActionIntentKind.MULTI_ACTION}:
            return GroundedAnswer(
                content=intent.message or "That project action is not supported.",
                source_refs=[],
                invalid_source_refs=[],
                retrieval_strategy="action_unsupported",
            )
        if intent.kind == ActionIntentKind.SUPPORTED and images:
            return GroundedAnswer(
                content=(
                    "AI Vision v1 is read-only. I can analyze the attached image, "
                    "but I cannot create a project action from image content yet."
                ),
                source_refs=[],
                invalid_source_refs=[],
                retrieval_strategy="image_action_unsupported",
            )
        if intent.kind == ActionIntentKind.SUPPORTED:
            if intent.action_type is None:  # pragma: no cover - detector invariant
                raise RuntimeError("Supported action intent has no action type")
            try:
                draft = await self.action_interpreter.interpret(
                    actor=actor,
                    question=question,
                    action_type=intent.action_type,
                    history_messages=history_messages,
                    known_members=known_members or [],
                )
            except (
                AIActionError,
                InvalidTransitionError,
                NotFoundError,
                PermissionDeniedError,
            ) as error:
                return GroundedAnswer(
                    content=str(error),
                    source_refs=[],
                    invalid_source_refs=[],
                    retrieval_strategy="action_validation",
                )
            return GroundedAnswer(
                content=(
                    f"🤖 Proposed Action\n\n{draft.summary}\n\n"
                    "Review the details and use Confirm or Cancel. "
                    "No project data has changed yet."
                ),
                source_refs=[],
                invalid_source_refs=[],
                retrieval_strategy="action_proposal",
                action_draft=draft,
            )
        plan = self.retrieval_planner.plan(question)
        context_records = self._retrieve_records(actor, plan)
        web_intent = self.web_research_intent_detector.detect(question)
        web_records: list[RetrievedContextRecord] = []
        if web_intent.required:
            if self.web_research_service is None or not self.web_research_service.configured:
                return GroundedAnswer(
                    content=(
                        "I need live web research for that, but the read-only web search "
                        "integration is not configured right now."
                    ),
                    source_refs=[],
                    invalid_source_refs=[],
                    retrieval_strategy="web_research_unavailable",
                )
            try:
                web_records = await self.web_research_service.search(question)
            except AIProviderError:
                return GroundedAnswer(
                    content=(
                        "I couldn't verify that on the live web right now, so I won't "
                        "pretend the answer is current. Try again later."
                    ),
                    source_refs=[],
                    invalid_source_refs=[],
                    retrieval_strategy="web_research_failed",
                )
            context_records = [*context_records, *web_records]

        prompt = self.prompt_builder.build(
            history_messages=history_messages,
            user_question=question,
            context_records=context_records,
            image_count=len(images),
        )
        provider_kwargs = {
            "system_instruction": prompt.system_instruction,
            "messages": prompt.messages,
            "context_records": prompt.context_records,
            "timeout_seconds": self.request_timeout,
        }
        if images:
            provider_kwargs["images"] = images
        response = await self.provider.generate(**provider_kwargs)
        valid, invalid = self._validate_source_refs(response.text, context_records)
        content = response.text
        cited_web_records = [
            record
            for record in web_records
            if record.source_id in valid and record.url
        ]
        if cited_web_records:
            sources = "\n".join(
                f"- [{record.source_id}] {record.title}: {record.url}"
                for record in cited_web_records
            )
            content = f"{content}\n\n**Web sources**\n{sources}"
        strategy = f"{plan.strategy}+web" if web_records else plan.strategy
        return GroundedAnswer(
            content=content,
            source_refs=valid,
            invalid_source_refs=invalid,
            retrieval_strategy=strategy,
        )

    def _retrieve_records(self, actor: Actor, plan: RetrievalPlan) -> list[RetrievedContextRecord]:
        strategy = plan.strategy
        if strategy == "github_links" and plan.entity_type is not None and plan.entity_id is not None:
            return self._records_from_github_search(
                self.project_context_service.get_linked_github_context(
                    actor,
                    entity_type=plan.entity_type,
                    entity_id=plan.entity_id,
                ),
                f"GitHub links for {plan.entity_type.upper()}-{plan.entity_id:03d}",
            )
        if strategy == "github_issues":
            return self._records_from_github_issues(
                self.project_context_service.get_open_github_issues(actor),
                "open GitHub issues",
            )
        if strategy in {"github_pull_requests", "github_failing_checks"}:
            pull_requests = self.project_context_service.get_open_pull_requests(actor)
            if strategy == "github_failing_checks":
                pull_requests = [item for item in pull_requests if item.get("checks_status") == "FAILING"]
            return self._records_from_github_pull_requests(pull_requests, strategy)
        if strategy == "github_recent_commits":
            return self._records_from_github_commits(
                self.project_context_service.get_recent_commits(actor),
                "recent GitHub commits",
            )
        if strategy == "github_pr_context" and plan.github_number is not None:
            pull_request = self.project_context_service.get_pr_context(actor, plan.github_number)
            return self._records_from_github_pull_requests(
                [pull_request] if pull_request else [],
                "GitHub pull request detail",
            )
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
            except NotFoundError:
                strategy = "search"
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
        records = self._records_from_search_results(matches, "project search")
        if len(records) < self.max_context_items:
            github_matches = self.project_context_service.search_github_context(actor, plan.query)
            records.extend(self._records_from_github_search(github_matches, "GitHub search"))
        return records[: self.max_context_items]

    def _records_from_github_issues(self, items: list[dict], reason: str) -> list[RetrievedContextRecord]:
        return [
            RetrievedContextRecord(
                source_type="github_issue",
                source_id=f"GH-ISSUE-{item['number']}",
                title=item.get("title") or f"GitHub Issue #{item['number']}",
                content=(
                    f"State: {item.get('state')}\nAuthor: {item.get('author')}\n"
                    f"Assignees: {item.get('assignees', [])}\nLabels: {item.get('labels', [])}\nURL: {item.get('url')}"
                ),
                timestamp=item.get("updated_at"),
                retrieval_reason=reason,
            )
            for item in items[: self.max_context_items]
        ]

    def _records_from_github_pull_requests(self, items: list[dict], reason: str) -> list[RetrievedContextRecord]:
        return [
            RetrievedContextRecord(
                source_type="github_pull_request",
                source_id=f"GH-PR-{item['number']}",
                title=item.get("title") or f"GitHub PR #{item['number']}",
                content=(
                    f"State: {item.get('state')}\nDraft: {item.get('draft')}\n"
                    f"Review: {item.get('review_status')}\nChecks: {item.get('checks_status')}\n"
                    f"Base: {item.get('base_branch')}\nHead: {item.get('head_branch')}\nURL: {item.get('url')}"
                ),
                timestamp=item.get("updated_at"),
                retrieval_reason=reason,
            )
            for item in items[: self.max_context_items]
        ]

    def _records_from_github_commits(self, items: list[dict], reason: str) -> list[RetrievedContextRecord]:
        return [
            RetrievedContextRecord(
                source_type="github_commit",
                source_id=f"GH-COMMIT-{item['short_sha']}",
                title=item.get("message") or item["short_sha"],
                content=f"Author: {item.get('author')}\nURL: {item.get('url')}",
                timestamp=item.get("committed_at"),
                retrieval_reason=reason,
            )
            for item in items[: self.max_context_items]
        ]

    def _records_from_github_search(self, items: list[dict], reason: str) -> list[RetrievedContextRecord]:
        records: list[RetrievedContextRecord] = []
        for item in items:
            item_type = item.get("item_type")
            if item_type == "issue":
                records.extend(self._records_from_github_issues([item], reason))
            elif item_type == "pull_request":
                records.extend(self._records_from_github_pull_requests([item], reason))
            elif item_type == "commit":
                records.extend(self._records_from_github_commits([item], reason))
        return records[: self.max_context_items]

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
