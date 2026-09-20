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


class RetrievalPlanner:
    def plan(self, question: str) -> RetrievalPlan:
        normalized = " ".join(question.lower().split())
        if any(phrase in normalized for phrase in ("what am i working on", "what i'm working on", "my current work", "current work")):
            return RetrievalPlan(strategy="current_work", query=question)
        if "blocker" in normalized or "blocked" in normalized:
            return RetrievalPlan(strategy="blockers", query=question)
        if any(phrase in normalized for phrase in ("project overview", "project status", "overview", "overall status")):
            return RetrievalPlan(strategy="overview", query=question)
        if "meeting-" in normalized:
            try:
                meeting_id = int(normalized.split("meeting-")[1][:3])
                return RetrievalPlan(strategy="meeting_context", query=question, meeting_id=meeting_id)
            except ValueError:
                pass
        if "meeting" in normalized:
            return RetrievalPlan(strategy="meeting_lookup", query=question, domains=("meetings",))
        if any(phrase in normalized for phrase in ("this week", "recent activity", "what happened")):
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
        return RetrievalPlan(strategy="fallback_search", query=question)
