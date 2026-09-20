from datetime import datetime

from cse_hq_bot.errors import InvalidInputError
from cse_hq_bot.identifiers import bug_code, decision_code, meeting_code, standup_code, task_code
from cse_hq_bot.models import Actor, BugStatus, MeetingStatus, Role, TaskStatus
from cse_hq_bot.permissions import ensure_can_view_member_context
from cse_hq_bot.services.activity_service import ActivityService
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService


class ProjectContextService:
    DEFAULT_ACTIVITY_LIMIT = 20
    DEFAULT_MEETING_LIMIT = 5
    DEFAULT_DECISION_LIMIT = 5
    DEFAULT_SEARCH_LIMIT = 10
    MAX_LIMIT = 50
    _SEARCH_DOMAINS = ("tasks", "bugs", "meetings", "decisions", "standups")

    def __init__(
        self,
        project_service: ProjectService,
        task_service: TaskService,
        bug_service: BugService,
        meeting_service: MeetingService,
        decision_service: DecisionService,
        standup_service: StandupService,
        activity_service: ActivityService,
    ):
        self.project_service = project_service
        self.task_service = task_service
        self.bug_service = bug_service
        self.meeting_service = meeting_service
        self.decision_service = decision_service
        self.standup_service = standup_service
        self.activity_service = activity_service

    def get_project_overview(self, actor: Actor) -> dict:
        dashboard = self.project_service.get_dashboard()
        active_meetings = [
            meeting
            for meeting in self.meeting_service.list_meetings(actor)
            if meeting.get("status") in {MeetingStatus.SCHEDULED.value, MeetingStatus.IN_PROGRESS.value}
        ][: self.DEFAULT_MEETING_LIMIT]
        return {
            "project": {
                "name": dashboard.name,
                "description": dashboard.description,
                "goal": dashboard.goal,
                "phase": dashboard.phase,
                "sprint": dashboard.sprint,
                "deadline": dashboard.deadline,
                "status": dashboard.status,
                "updated_at": dashboard.updated_at.isoformat(sep=" ", timespec="seconds"),
            },
            "task_statistics": self.task_service.task_statistics(actor),
            "bug_statistics": self.bug_service.bug_statistics(actor),
            "active_meetings": active_meetings,
            "recent_decisions": self.decision_service.list_decisions(actor)[: self.DEFAULT_DECISION_LIMIT],
            "standup_summary": self.standup_service.weekly_standup_summary(),
        }

    def get_current_work(self, actor: Actor, user_id: str | None = None) -> dict:
        subject_id = (user_id or actor.user_id).strip()
        ensure_can_view_member_context(actor, subject_id)
        tasks = [
            task
            for task in self.task_service.list_accessible_tasks(actor)
            if task.get("assignee_id") == subject_id
        ]
        today = self.standup_service.today_for_actor(actor)
        standup = next(
            (
                entry
                for entry in self.standup_service.list_for_date(actor, today)
                if entry.get("user_id") == subject_id
            ),
            None,
        )
        open_bugs = [
            bug
            for bug in self.bug_service.list_open_bugs(actor)
            if subject_id in {bug.get("assignee_id"), bug.get("created_by")}
        ]
        return {
            "user_id": subject_id,
            "active_tasks": [
                task
                for task in tasks
                if task.get("status") in {TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value}
                and task.get("status") != TaskStatus.BLOCKED.value
            ],
            "blocked_tasks": [task for task in tasks if task.get("status") == TaskStatus.BLOCKED.value],
            "current_standup": standup,
            "open_bugs": open_bugs,
        }

    def get_blockers(self, actor: Actor) -> dict:
        return {
            "blocked_tasks": self.task_service.filter_tasks(actor, status=TaskStatus.BLOCKED.value),
            "high_severity_bugs": [
                bug
                for bug in self.bug_service.list_open_bugs(actor)
                if int(bug.get("severity") or 0) >= 4
            ],
            "standups_with_blockers": [
                standup
                for standup in self.standup_service.list_recent(actor, days=7)
                if standup.get("blockers")
            ],
        }

    def get_meeting_context(self, actor: Actor, meeting_id: int) -> dict:
        meeting = self.meeting_service.get_meeting(actor, meeting_id)
        return {
            "meeting": meeting,
            "participants": self.meeting_service.list_participants(actor, meeting_id),
            "notes": self.meeting_service.get_notes(actor, meeting_id),
            "decisions": self.decision_service.list_decisions_for_meeting(actor, meeting_id),
            "tasks": [
                task
                for task in self.task_service.list_accessible_tasks(actor)
                if task.get("source_meeting_id") == meeting_id
            ],
        }

    def search_decisions(self, actor: Actor, query: str) -> list[dict]:
        return self.decision_service.search_decisions(actor, query)

    def get_recent_activity(self, actor: Actor, limit: int = DEFAULT_ACTIVITY_LIMIT) -> list[dict]:
        return self._collect_accessible_activity(
            actor,
            limit,
            lambda batch_limit, offset: self.activity_service.list_recent_activity(
                limit=batch_limit,
                offset=offset,
            ),
        )

    def get_activity_between(
        self,
        actor: Actor,
        start_time: str,
        end_time: str,
        *,
        limit: int = DEFAULT_ACTIVITY_LIMIT,
    ) -> list[dict]:
        return self._collect_accessible_activity(
            actor,
            limit,
            lambda batch_limit, offset: self.activity_service.list_activity_between(
                start_time,
                end_time,
                limit=batch_limit,
                offset=offset,
            ),
        )

    def search_project_memory(
        self,
        actor: Actor,
        query: str,
        domains: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[dict]:
        needle = query.strip()
        if not needle:
            raise InvalidInputError("Search text is required")
        normalized_domains = list(domains or self._SEARCH_DOMAINS)
        invalid_domains = [domain for domain in normalized_domains if domain not in self._SEARCH_DOMAINS]
        if invalid_domains:
            raise InvalidInputError(f"Unsupported search domains: {', '.join(sorted(set(invalid_domains)))}")

        results: list[dict] = []
        if "tasks" in normalized_domains:
            results.extend(
                self._task_search_result(task, needle)
                for task in self.task_service.filter_tasks(actor, search=needle)
            )
        if "bugs" in normalized_domains:
            results.extend(
                self._bug_search_result(bug, needle)
                for bug in self.bug_service.filter_bugs(actor, search=needle)
            )
        if "meetings" in normalized_domains:
            results.extend(
                self._meeting_search_result(meeting, needle)
                for meeting in self.meeting_service.search_meetings(actor, needle)
            )
        if "decisions" in normalized_domains:
            results.extend(
                self._decision_search_result(decision, needle)
                for decision in self.decision_service.search_decisions(actor, needle)
            )
        if "standups" in normalized_domains:
            results.extend(
                self._standup_search_result(standup, needle)
                for standup in self.standup_service.search_standups(actor, needle)
            )
            results.sort(
                key=lambda item: (
                    0 if item["relevance_hint"].startswith("title") else 1,
                    -self._timestamp_sort_key(item["timestamp"]),
                    item["source_id"],
                )
            )
            return results[: self._normalize_limit(limit)]

    def _collect_accessible_activity(self, actor: Actor, limit: int, loader) -> list[dict]:
        target_limit = self._normalize_limit(limit)
        batch_size = min(max(target_limit, self.DEFAULT_ACTIVITY_LIMIT), ActivityService.MAX_LIMIT)
        visible: list[dict] = []
        access = self._build_activity_access(actor)
        offset = 0
        while len(visible) < target_limit:
            batch = loader(batch_size, offset)
            if not batch:
                break
            for activity in batch:
                if self._can_access_activity(actor, activity, access):
                    visible.append(activity)
                    if len(visible) >= target_limit:
                        break
            if len(batch) < batch_size:
                break
            offset += len(batch)
        return visible

    def _build_activity_access(self, actor: Actor) -> dict:
        return {
            "task_ids": {task_code(task) for task in self.task_service.list_accessible_tasks(actor)},
            "bug_ids": {bug_code(bug) for bug in self.bug_service.list_accessible_bugs(actor)},
            "standup_user_id": None if actor.role in {Role.LEADER, Role.CO_LEAD} else actor.user_id,
        }

    def _can_access_activity(self, actor: Actor, activity: dict, access: dict) -> bool:
        metadata = activity.get("metadata") or {}
        entity_type = activity.get("entity_type")
        if entity_type == "task":
            return activity.get("entity_id") in access["task_ids"]
        if entity_type == "bug":
            return activity.get("entity_id") in access["bug_ids"]
        if entity_type in {"meeting", "decision"}:
            return True
        if entity_type == "standup":
            standup_user_id = access["standup_user_id"]
            return standup_user_id is None or metadata.get("user_id") == standup_user_id
        return False

    def _normalize_limit(self, limit: int) -> int:
        return max(1, min(int(limit), self.MAX_LIMIT))

    def _task_search_result(self, task: dict, needle: str) -> dict:
        return self._search_result(
            source_type="task",
            source_id=task_code(task),
            title=task["title"],
            snippet=self._snippet(needle, task["title"], task["description"]),
            timestamp=task.get("created_at"),
            relevance_hint=self._relevance_hint(needle, task["title"], "task text match"),
        )

    def _bug_search_result(self, bug: dict, needle: str) -> dict:
        return self._search_result(
            source_type="bug",
            source_id=bug_code(bug),
            title=bug["title"],
            snippet=self._snippet(needle, bug["title"], bug["description"]),
            timestamp=bug.get("created_at"),
            relevance_hint=self._relevance_hint(needle, bug["title"], "bug text match"),
        )

    def _meeting_search_result(self, meeting: dict, needle: str) -> dict:
        return self._search_result(
            source_type="meeting",
            source_id=meeting_code(meeting),
            title=meeting["title"],
            snippet=self._snippet(needle, meeting.get("title", ""), meeting.get("description", ""), meeting.get("agenda", "")),
            timestamp=meeting.get("updated_at") or meeting.get("created_at") or meeting.get("scheduled_at"),
            relevance_hint=self._relevance_hint(needle, meeting["title"], "meeting text match"),
        )

    def _decision_search_result(self, decision: dict, needle: str) -> dict:
        return self._search_result(
            source_type="decision",
            source_id=decision_code(decision),
            title=decision.get("title") or decision.get("decision") or "Untitled decision",
            snippet=self._snippet(
                needle,
                decision.get("title", ""),
                decision.get("decision", ""),
                decision.get("context", ""),
                decision.get("rationale", ""),
            ),
            timestamp=decision.get("updated_at") or decision.get("created_at"),
            relevance_hint=self._relevance_hint(needle, decision.get("title") or "", "decision text match"),
        )

    def _standup_search_result(self, standup: dict, needle: str) -> dict:
        return self._search_result(
            source_type="standup",
            source_id=standup_code(standup),
            title=f"Standup for {standup['user_id']} on {standup['date']}",
            snippet=self._snippet(needle, standup.get("previous", ""), standup.get("current", ""), standup.get("blockers", "")),
            timestamp=standup.get("updated_at") or standup.get("created_at"),
            relevance_hint="standup text match",
        )

    def _search_result(
        self,
        *,
        source_type: str,
        source_id: str,
        title: str,
        snippet: str,
        timestamp: str | None,
        relevance_hint: str,
    ) -> dict:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "snippet": snippet,
            "timestamp": timestamp,
            "relevance_hint": relevance_hint,
        }

    def _relevance_hint(self, needle: str, title: str, fallback: str) -> str:
        return "title match" if needle.lower() in title.lower() else fallback

    def _snippet(self, needle: str, *parts: str) -> str:
        haystack = " | ".join(part.strip() for part in parts if part and part.strip())
        if not haystack:
            return ""
        lower_haystack = haystack.lower()
        lower_needle = needle.lower()
        index = lower_haystack.find(lower_needle)
        if index == -1:
            return haystack[:160]
        start = max(index - 30, 0)
        end = min(index + len(needle) + 90, len(haystack))
        return haystack[start:end]

    def _timestamp_sort_key(self, value: str | None) -> float:
        if not value:
            return float("-inf")
        normalized = value.replace("Z", "+00:00")
        if "T" not in normalized and " " in normalized:
            normalized = normalized.replace(" ", "T", 1)
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return float("-inf")
