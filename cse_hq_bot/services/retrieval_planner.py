import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class RetrievalPlan:
    strategy: str
    query: str
    start_time: str | None = None
    end_time: str | None = None
    domains: tuple[str, ...] = ()
    meeting_id: int | None = None
    github_number: int | None = None
    entity_type: str | None = None
    entity_id: int | None = None


class RetrievalPlanner:
    def plan(self, question: str) -> RetrievalPlan:
        normalized = " ".join(question.lower().split())
        linked_match = re.search(r"\b(task|bug)-(\d+)\b", normalized)
        if linked_match and any(term in normalized for term in ("link", "github", "issue", "pull request", "pr")):
            return RetrievalPlan(
                strategy="github_links",
                query=question,
                entity_type=linked_match.group(1),
                entity_id=int(linked_match.group(2)),
            )
        gh_pr_match = re.search(r"(?:gh-pr-|pull request\s*#?|pr\s*#?)(\d+)", normalized)
        if gh_pr_match:
            return RetrievalPlan(
                strategy="github_pr_context",
                query=question,
                github_number=int(gh_pr_match.group(1)),
            )
        if any(phrase in normalized for phrase in ("failing ci", "failed ci", "blocked by ci", "checks failing")):
            return RetrievalPlan(strategy="github_failing_checks", query=question)
        if any(phrase in normalized for phrase in ("open prs", "open pull requests", "prs are open", "pull requests are open")):
            return RetrievalPlan(strategy="github_pull_requests", query=question)
        if any(phrase in normalized for phrase in ("github issues", "open issues", "issues are still open")):
            return RetrievalPlan(strategy="github_issues", query=question)
        if any(phrase in normalized for phrase in ("changed in the repo", "recent commits", "repository changes", "repo recently")):
            return RetrievalPlan(strategy="github_recent_commits", query=question)
        if any(
            phrase in normalized
            for phrase in (
                "what am i working on",
                "what am i currently working on",
                "what i'm working on",
                "what i'm currently working on",
                "my current work",
                "current work",
                "what are my tasks",
                "my active tasks",
                "mình đang làm gì",
                "tôi đang làm gì",
                "task của mình",
                "task của tôi",
            )
        ):
            return RetrievalPlan(strategy="current_work", query=question)
        if "blocker" in normalized or "blocked" in normalized:
            return RetrievalPlan(strategy="blockers", query=question)
        if any(phrase in normalized for phrase in ("project overview", "project status", "overview", "overall status")):
            return RetrievalPlan(strategy="overview", query=question)
        if "meeting-" in normalized:
            match = re.search(r"meeting-(\d+)", normalized)
            if match:
                return RetrievalPlan(strategy="meeting_context", query=question, meeting_id=int(match.group(1)))
        if any(phrase in normalized for phrase in ("this week", "recent activity", "recently")):
            end_time = datetime.now(UTC)
            start_time = end_time - timedelta(days=7)
            return RetrievalPlan(
                strategy="recent_activity",
                query=question,
                start_time=start_time.isoformat(sep=" ", timespec="seconds"),
                end_time=end_time.isoformat(sep=" ", timespec="seconds"),
            )
        if any(phrase in normalized for phrase in ("why did we choose", "decision rationale", "why was", "why did")):
            return RetrievalPlan(strategy="decision_reasoning", query=question, domains=("decisions", "meetings"))
        if "meeting" in normalized:
            return RetrievalPlan(strategy="meeting_lookup", query=question, domains=("meetings",))
        return RetrievalPlan(strategy="fallback_search", query=question)
