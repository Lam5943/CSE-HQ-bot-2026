import logging
from collections.abc import Callable

import discord

from cse_hq_bot.ai.action_models import ActionProposal
from cse_hq_bot.errors import (
    AIActionAlreadyHandledError,
    AIActionConflictError,
    AIActionExpiredError,
    AIActionOwnershipError,
    AIActionValidationError,
    CSEHQError,
    InvalidInputError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
)
from cse_hq_bot.health_models import HealthReport, HealthState
from cse_hq_bot.models import (
    Actor,
    BugStatus,
    MeetingStatus,
    ProjectDashboard,
    TaskStatus,
)
from cse_hq_bot.permissions import ensure_can_modify_bug, ensure_can_modify_task
from cse_hq_bot.services.ai_action_service import AIActionService
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.github_service import GitHubService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService
from cse_hq_bot.ui_theme import list_entry, metric_value, set_surface_footer, surface_embed

logger = logging.getLogger(__name__)

PAGE_SIZE = 8
STALE_TASK_MESSAGE = "This task is no longer available in its previous state. Please refresh the task list."
STALE_BUG_MESSAGE = "This bug is no longer available in its previous state. Please refresh the bug list."
STALE_MEETING_MESSAGE = "This meeting is no longer available in its previous state. Please refresh the meetings list."
STALE_DECISION_MESSAGE = "This decision is no longer available in its previous state. Please refresh the decisions list."

AI_RESPONSE_LIMIT = 1800


def build_health_embed(report: HealthReport) -> discord.Embed:
    colors = {
        HealthState.HEALTHY: discord.Color.green(),
        HealthState.DEGRADED: discord.Color.orange(),
        HealthState.DISABLED: discord.Color.light_grey(),
        HealthState.FAILED: discord.Color.red(),
    }
    icons = {
        HealthState.HEALTHY: "✅",
        HealthState.DEGRADED: "⚠️",
        HealthState.DISABLED: "➖",
        HealthState.FAILED: "❌",
    }
    embed = discord.Embed(
        title="🩺 CSE-HQ • System Health",
        description=(
            f"Overall: **{report.overall.value}**\n"
            f"Application version: `{report.version}`"
        ),
        color=colors[report.overall],
    )
    for component in report.components:
        embed.add_field(
            name=component.label,
            value=(
                f"{icons[component.state]} **{component.state.value}** — "
                f"{component.detail}"
            )[:1024],
            inline=False,
        )
    embed.set_footer(
        text=f"Checked {report.checked_at.isoformat(timespec='seconds')}"
    )
    return embed


def _ai_action_error_message(error: CSEHQError) -> tuple[str, bool]:
    if isinstance(error, AIActionExpiredError):
        return "This action proposal expired. Create and review a new proposal.", True
    if isinstance(error, AIActionConflictError):
        return (
            (
                "The project record changed after this action was proposed. "
                "Refresh the project state and try again."
            ),
            True,
        )
    if isinstance(error, AIActionAlreadyHandledError):
        return "This action proposal has already been handled.", True
    if isinstance(error, AIActionOwnershipError):
        return "Only the proposal creator may handle this action.", False
    if isinstance(error, PermissionDeniedError):
        return "You no longer have permission to perform this action.", True
    if isinstance(error, AIActionValidationError):
        return str(error), True
    return "This action could not be completed safely.", True


def split_ai_response(text: str, limit: int = AI_RESPONSE_LIMIT) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return [""]
    chunks: list[str] = []
    remaining = cleaned
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit + 1)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at == -1:
            split_at = limit
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def build_ai_home_embed() -> discord.Embed:
    embed = surface_embed("ai", description="### Private teammate workspace")
    embed.description = (
        "Private, project-grounded help from a mentor-style teammate, plus "
        "explicitly confirmed internal actions."
    )
    embed.add_field(
        name="🧠 Capabilities",
        value=(
            "Grounded Q&A, PNG/JPEG/WEBP image analysis, plus bounded Task, Bug, "
            "Meeting, Decision, and own Standup action proposals"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ Boundaries",
        value=(
            "Read-only by default. No mutation occurs without your explicit "
            "confirmation; GitHub remains read-only."
        ),
        inline=False,
    )
    embed.add_field(
        name="✨ Personality",
        value=(
            "Experienced mentor, low-pressure teammate, and lightly funny when the "
            "situation allows it. CSE-HQ challenges weak assumptions without judging people."
        ),
        inline=False,
    )
    embed.add_field(name="⚡ Actions", value="New Session • My Sessions", inline=False)
    return embed


def build_ai_sessions_embed(sessions: list[dict]) -> discord.Embed:
    embed = surface_embed("ai", title="My Sessions")
    if not sessions:
        embed.description = "No AI sessions found."
        return embed
    embed.description = "\n".join(
        (
            f"<#{session['discord_thread_id']}> • "
            f"**{str(session['status']).replace('_', ' ').title()}** • "
            f"last active {session['last_active_at']}"
        )
        for session in sessions[:10]
    )
    return embed


def build_ai_session_intro_embed(session: dict) -> discord.Embed:
    embed = surface_embed(
        "ai",
        title="Private Session",
        description="### Ask naturally",
    )
    embed.description = (
        "Ask project questions, attach PNG/JPEG/WEBP screenshots for read-only "
        "analysis, or propose one supported internal action. The assistant is private, "
        "permission-aware, project-grounded, and behaves like an experienced low-pressure "
        "teammate rather than a manager."
    )
    embed.add_field(
        name="📚 Source of truth",
        value="Current CSE-HQ project records always win over prior AI replies.",
        inline=False,
    )
    embed.add_field(
        name="🛡️ Boundaries",
        value=(
            "Actions require Confirm, expire automatically, and are revalidated "
            "before execution. GitHub remains read-only."
        ),
        inline=False,
    )
    return embed


def build_ai_action_embed(proposal: ActionProposal) -> discord.Embed:
    embed = surface_embed(
        "ai",
        title="Action Proposal",
        description=f"### Proposed change\n{proposal.summary}",
        color=discord.Color.gold(),
    )
    embed.add_field(name="📌 Status", value=proposal.status, inline=True)
    embed.add_field(name="⏳ Expires", value=proposal.expires_at, inline=True)
    embed.add_field(
        name="🛡️ Safety",
        value=(
            "No project data has changed. The action executes only after the proposal "
            "owner presses Confirm, and permissions/state are checked again."
        ),
        inline=False,
    )
    return embed


def _trim(value: str, *, default: str = "N/A") -> str:
    stripped = value.strip()
    return stripped or default


def _optional(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return int(stripped)


def _truncate(value: str, limit: int = 80) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}…"


def _page_slice(items: list[dict], page: int, page_size: int = PAGE_SIZE) -> tuple[list[dict], int, int]:
    safe_page, total_pages, _, _ = _pagination_state(len(items), page, page_size)
    if not items:
        return [], 0, 1
    start = safe_page * page_size
    end = start + page_size
    return items[start:end], safe_page, total_pages


def _pagination_state(
    total_items: int, page: int, page_size: int = PAGE_SIZE
) -> tuple[int, int, bool, bool]:
    if total_items <= 0:
        return 0, 1, True, True
    max_page = max((total_items - 1) // page_size, 0)
    safe_page = max(0, min(page, max_page))
    return safe_page, max_page + 1, safe_page <= 0, safe_page >= max_page


def _status_badge(status: str) -> str:
    return status.replace("_", " ").title()


def _meeting_code(meeting: dict) -> str:
    return str(meeting.get("code") or f"MEETING-{int(meeting['id']):03d}")


def _decision_code(decision: dict) -> str:
    return str(decision.get("code") or f"DEC-{int(decision['id']):03d}")


def _dashboard_status_style(status: str) -> tuple[str, discord.Color]:
    normalized = " ".join(status.strip().lower().replace("_", " ").split())
    if any(
        marker in normalized
        for marker in ("blocked", "critical", "off track", "red")
    ):
        return "🔴", discord.Color.red()
    if any(
        marker in normalized
        for marker in ("at risk", "risk", "warning", "delayed", "delay")
    ):
        return "🟠", discord.Color.orange()
    if any(
        marker in normalized
        for marker in ("on track", "healthy", "done", "complete", "green")
    ):
        return "🟢", discord.Color.green()
    return "🔵", discord.Color.blurple()


def _dashboard_task_progress(dashboard: ProjectDashboard, width: int = 14) -> str:
    if dashboard.task_total <= 0:
        return f"`{'░' * width}` **0%**\nNo tasks tracked yet."
    ratio = max(0.0, min(1.0, dashboard.task_done / dashboard.task_total))
    filled = min(width, max(0, int(ratio * width + 0.5)))
    bar = "█" * filled + "░" * (width - filled)
    percentage = round(ratio * 100)
    return (
        f"`{bar}` **{percentage}%**\n"
        f"{dashboard.task_done} of {dashboard.task_total} tasks completed"
    )


def build_dashboard_embed(dashboard: ProjectDashboard) -> discord.Embed:
    status_icon, color = _dashboard_status_style(dashboard.status)
    description = _trim(dashboard.description, default="No project description yet.")
    phase = _trim(dashboard.phase)
    sprint = _trim(dashboard.sprint)
    deadline = _trim(dashboard.deadline)
    status = _trim(dashboard.status)

    embed = surface_embed(
        "project",
        title="Project Command Center",
        description=(
            f"### {dashboard.name}\n"
            f"> {description}\n\n"
            f"{status_icon} **{status}**  ·  🧭 {phase}  ·  🏃 {sprint}"
        ),
        color=color,
        timestamp=dashboard.updated_at,
    )
    embed.add_field(
        name="🎯 Current Objective",
        value=f"**{_trim(dashboard.goal)}**",
        inline=False,
    )
    embed.add_field(
        name="📈 Delivery Progress",
        value=_dashboard_task_progress(dashboard),
        inline=False,
    )
    embed.add_field(name="✅ Completed", value=f"**{dashboard.task_done}**\nTasks", inline=True)
    embed.add_field(name="🧩 Open", value=f"**{dashboard.task_open}**\nTasks", inline=True)
    embed.add_field(name="🐞 Bugs", value=f"**{dashboard.bug_open}**\nOpen", inline=True)
    embed.add_field(
        name="🗓 Meetings",
        value=f"**{dashboard.meetings_total}**\nRecorded",
        inline=True,
    )
    embed.add_field(name="📅 Deadline", value=f"**{deadline}**", inline=True)
    embed.add_field(
        name="📦 Scope",
        value=f"{dashboard.task_total} tasks · {dashboard.bug_total} bugs",
        inline=True,
    )
    set_surface_footer(embed, "project", detail="Live overview")
    return embed

def build_weekly_dashboard_embed(
    dashboard: ProjectDashboard,
    week_key: str,
) -> discord.Embed:
    embed = build_dashboard_embed(dashboard)
    description = _trim(dashboard.description, default="No project description yet.")
    embed.title = "📆 CSE-HQ • Weekly Pulse"
    embed.description = (
        f"### {dashboard.name} · {week_key}\n"
        f"> {description}\n\n"
        "Team snapshot for the current ISO week."
    )
    set_surface_footer(embed, "project", detail=f"Weekly Pulse • {week_key}")
    return embed


def build_tasks_embed(
    tasks: list[dict],
    *,
    page: int = 0,
    mode_label: str = "All Tasks",
    filters_label: str = "None",
    stats: dict | None = None,
) -> discord.Embed:
    embed = surface_embed("tasks", description="### Work queue\nTrack ownership, urgency, and execution state.")
    stats = stats or {}
    embed.add_field(name="📚 Scope", value=mode_label, inline=True)
    embed.add_field(name="🔎 Filters", value=filters_label, inline=True)
    embed.add_field(name="📦 Total", value=metric_value(len(tasks), "Tasks"), inline=True)
    if stats:
        embed.add_field(name="👤 My Tasks", value=metric_value(stats.get("my_tasks", 0), "Tasks"), inline=True)
        embed.add_field(name="🔥 High Priority", value=metric_value(stats.get("high_priority", 0), "Tasks"), inline=True)
        embed.add_field(name="📅 With Deadline", value=metric_value(stats.get("with_deadline", 0), "Tasks"), inline=True)
    if not tasks:
        embed.description += "\n\n> No tasks found."
        return embed
    page_items, safe_page, total_pages = _page_slice(tasks, page)
    lines = [
        list_entry(
            f"TASK-{int(task['id']):03d} · {_truncate(task['title'])}",
            f"{_status_badge(task['status'])} · P{task['priority']}",
        )
        for task in page_items
    ]
    embed.description += "\n\n" + "\n\n".join(lines)
    set_surface_footer(
        embed,
        "tasks",
        detail=f"Page {safe_page + 1}/{total_pages} • {len(page_items)}/{len(tasks)} shown",
    )
    return embed


def build_task_detail_embed(task: dict, *, can_modify: bool) -> discord.Embed:
    embed = surface_embed(
        "tasks",
        title=f"TASK-{int(task['id']):03d}",
        description=f"### {_truncate(task['title'], 120)}\n{task['description'] or 'No description.'}",
    )
    embed.add_field(name="Status", value=_status_badge(task["status"]), inline=True)
    embed.add_field(name="Priority", value=f"P{task['priority']}", inline=True)
    embed.add_field(name="Assignee", value=task.get("assignee_id") or "Unassigned", inline=True)
    embed.add_field(name="Creator", value=task.get("created_by") or "Unknown", inline=True)
    embed.add_field(name="Deadline", value=task.get("deadline") or "N/A", inline=True)
    embed.add_field(name="Created", value=task.get("created_at", "N/A"), inline=True)
    embed.add_field(
        name="Available Actions",
        value=(
            "Start, Block, Reopen, Complete, Assign, Edit"
            if can_modify
            else "View only (you are not authorized to modify this task)"
        ),
        inline=False,
    )
    return embed


def build_bugs_embed(
    bugs: list[dict],
    *,
    show_all: bool = False,
    page: int = 0,
    filters_label: str = "None",
    stats: dict | None = None,
) -> discord.Embed:
    title = "All Bugs" if show_all else "Open Bugs"
    embed = surface_embed("bugs", title=title, description="### Defect queue\nSee what is broken, how severe it is, and what needs attention.")
    stats = stats or {}
    embed.add_field(name="🔎 Filters", value=filters_label, inline=True)
    embed.add_field(name="📦 Total", value=metric_value(len(bugs), "Bugs"), inline=True)
    embed.add_field(name="🚨 Open", value=metric_value(stats.get("open", 0), "Bugs"), inline=True)
    if stats:
        embed.add_field(name="🔥 Critical", value=metric_value(stats.get("critical", 0), "Bugs"), inline=True)
        embed.add_field(name="👤 Unassigned", value=metric_value(stats.get("unassigned", 0), "Bugs"), inline=True)
        embed.add_field(
            name="Resolved",
            value=str(stats.get("by_status", {}).get(BugStatus.RESOLVED.value, 0)),
            inline=True,
        )
    if not bugs:
        embed.description += "\n\n> No bugs found."
        return embed
    page_items, safe_page, total_pages = _page_slice(bugs, page)
    lines = [
        list_entry(
            f"BUG-{int(bug['id']):03d} · {_truncate(bug['title'])}",
            f"{_status_badge(bug['status'])} · Severity {bug['severity']}",
        )
        for bug in page_items
    ]
    embed.description += "\n\n" + "\n\n".join(lines)
    set_surface_footer(
        embed,
        "bugs",
        detail=f"Page {safe_page + 1}/{total_pages} • {len(page_items)}/{len(bugs)} shown",
    )
    return embed


def build_bug_detail_embed(bug: dict, *, can_modify: bool) -> discord.Embed:
    embed = surface_embed(
        "bugs",
        title=f"BUG-{int(bug['id']):03d}",
        description=f"### {_truncate(bug['title'], 120)}\n{bug['description'] or 'No description.'}",
        color=discord.Color.red(),
    )
    embed.add_field(name="Status", value=_status_badge(bug["status"]), inline=True)
    embed.add_field(name="Severity", value=f"S{bug['severity']}", inline=True)
    embed.add_field(name="Assignee", value=bug.get("assignee_id") or "Unassigned", inline=True)
    embed.add_field(name="Reporter", value=bug.get("created_by") or "Unknown", inline=True)
    embed.add_field(name="Created", value=bug.get("created_at", "N/A"), inline=True)
    embed.add_field(name="Resolved At", value=bug.get("resolved_at") or "N/A", inline=True)
    embed.add_field(
        name="Available Actions",
        value=(
            "Set Status, Assign, Edit, Resolve/Reopen"
            if can_modify
            else "View only (you are not authorized to modify this bug)"
        ),
        inline=False,
    )
    return embed


def build_meetings_embed(
    meetings: list[dict],
    *,
    page: int = 0,
    mode_label: str = "Upcoming",
) -> discord.Embed:
    embed = surface_embed("meetings", description="### Team syncs\nUpcoming, active, and historical collaboration sessions.")
    embed.add_field(name="📚 Scope", value=mode_label, inline=True)
    embed.add_field(name="📦 Total", value=metric_value(len(meetings), "Meetings"), inline=True)
    embed.add_field(
        name="Status",
        value=(
            f"Scheduled: {len([meeting for meeting in meetings if meeting.get('status') == MeetingStatus.SCHEDULED.value])}\n"
            f"Active: {len([meeting for meeting in meetings if meeting.get('status') == MeetingStatus.IN_PROGRESS.value])}"
        ),
        inline=True,
    )
    if not meetings:
        embed.description += "\n\n> No meetings found."
        return embed
    page_items, safe_page, total_pages = _page_slice(meetings, page)
    embed.description = "\n".join(
        f"`{_meeting_code(meeting)}` "
        f"[{_status_badge(meeting['status'])}] "
        f"{_truncate(meeting['title'])} — {_truncate(meeting.get('scheduled_at') or meeting.get('meeting_date') or 'N/A', 40)}"
        for meeting in page_items
    )
    embed.set_footer(
        text=f"Page {safe_page + 1}/{total_pages} • Showing {len(page_items)}/{len(meetings)} meetings"
    )
    return embed


def build_meeting_detail_embed(
    meeting: dict,
    *,
    participants: list[dict],
    notes: list[dict],
    can_manage: bool,
    can_add_note: bool,
) -> discord.Embed:
    embed = surface_embed(
        "meetings",
        title=_meeting_code(meeting),
        description=f"### {_truncate(meeting['title'], 120)}\n{meeting.get('description') or 'No description.'}",
    )
    embed.add_field(name="Status", value=_status_badge(meeting["status"]), inline=True)
    embed.add_field(
        name="Scheduled",
        value=meeting.get("scheduled_at") or meeting.get("meeting_date") or "N/A",
        inline=True,
    )
    embed.add_field(name="Creator", value=meeting.get("created_by") or "Unknown", inline=True)
    participant_ids = [participant["user_id"] for participant in participants]
    embed.add_field(
        name=f"Participants ({len(participant_ids)})",
        value="\n".join(participant_ids[:5]) if participant_ids else "No participants yet.",
        inline=False,
    )
    embed.add_field(name="Agenda", value=_trim(meeting.get("agenda") or "", default="No agenda."), inline=False)
    latest_notes = notes[:3]
    embed.add_field(
        name="Latest Notes",
        value=(
            "\n".join(
                f"- {note['author_id']}: {_truncate(note['content'], 120)}"
                for note in latest_notes
            )
            if latest_notes
            else "No notes yet."
        ),
        inline=False,
    )
    actions: list[str] = ["Refresh", "Back"]
    if can_manage and meeting["status"] == MeetingStatus.SCHEDULED.value:
        actions.extend(["Start", "Cancel", "Add Participant", "Remove Participant"])
    if can_manage and meeting["status"] == MeetingStatus.IN_PROGRESS.value:
        actions.extend(["Complete", "Add Participant", "Remove Participant", "Record Decision"])
    if can_manage and meeting["status"] == MeetingStatus.COMPLETED.value:
        actions.append("Record Decision")
    if can_add_note:
        actions.append("Add Note")
    actions.append("Create Action Task")
    embed.add_field(name="Available Actions", value=", ".join(actions), inline=False)
    return embed


def build_decisions_embed(
    decisions: list[dict],
    *,
    page: int = 0,
    mode_label: str = "Browse",
) -> discord.Embed:
    embed = surface_embed("decisions", description="### Decision memory\nImportant choices, rationale, and context.")
    embed.add_field(name="📚 Scope", value=mode_label, inline=True)
    embed.add_field(name="📦 Total", value=metric_value(len(decisions), "Decisions"), inline=True)
    embed.add_field(
        name="🔗 Linked Meetings",
        value=str(len([decision for decision in decisions if decision.get("meeting_id")])),
        inline=True,
    )
    if not decisions:
        embed.description += "\n\n> No decisions found."
        return embed
    page_items, safe_page, total_pages = _page_slice(decisions, page)
    embed.description = "\n".join(
        f"`{_decision_code(decision)}` "
        f"{_truncate(decision.get('title') or decision.get('summary') or 'Untitled')} "
        f"— {_truncate(decision.get('decision') or '', 50)}"
        for decision in page_items
    )
    embed.set_footer(
        text=f"Page {safe_page + 1}/{total_pages} • Showing {len(page_items)}/{len(decisions)} decisions"
    )
    return embed


def build_decision_detail_embed(decision: dict, *, can_edit: bool) -> discord.Embed:
    embed = surface_embed(
        "decisions",
        title=_decision_code(decision),
        description=(
            f"### {_truncate(decision.get('title') or 'Untitled', 120)}\n"
            f"{decision.get('decision') or decision.get('summary') or 'No decision text.'}"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(name="Meeting", value=str(decision.get("meeting_id") or "Standalone"), inline=True)
    embed.add_field(name="Recorder", value=decision.get("created_by") or decision.get("decided_by") or "Unknown", inline=True)
    embed.add_field(name="Updated", value=decision.get("updated_at") or decision.get("created_at") or "N/A", inline=True)
    embed.add_field(name="Context", value=_trim(decision.get("context") or "", default="None"), inline=False)
    embed.add_field(name="Rationale", value=_trim(decision.get("rationale") or "", default="None"), inline=False)
    embed.add_field(
        name="Alternatives",
        value=_trim(decision.get("alternatives") or "", default="None"),
        inline=False,
    )
    embed.add_field(
        name="Available Actions",
        value="Edit, Refresh, Back" if can_edit else "Refresh, Back",
        inline=False,
    )
    return embed


def build_standup_embed(
    today_entry: dict | None,
    entries: list[dict],
    *,
    page: int = 0,
    mode_label: str = "Today's Team",
) -> discord.Embed:
    embed = surface_embed("standup", description="### Daily pulse\nCurrent work, momentum, and blockers.")
    embed.add_field(
        name="🙋 Today's Status",
        value="Submitted" if today_entry else "Not submitted",
        inline=True,
    )
    embed.add_field(name="📚 Scope", value=mode_label, inline=True)
    embed.add_field(name="📦 Total", value=metric_value(len(entries), "Updates"), inline=True)
    if today_entry:
        embed.add_field(
            name="📝 My Update",
            value=(
                f"Previous: {_truncate(today_entry.get('previous') or '', 80)}\n"
                f"Current: {_truncate(today_entry.get('current') or '', 80)}\n"
                f"Blockers: {_trim(today_entry.get('blockers') or '', default='None')}"
            ),
            inline=False,
        )
    if not entries:
        embed.description += "\n\n> No standups found."
        return embed
    page_items, safe_page, total_pages = _page_slice(entries, page)
    embed.description = "\n".join(
        f"`{entry.get('date')}` {entry.get('user_id')} — "
        f"{_truncate(entry.get('current') or entry.get('update_text') or '', 90)}"
        for entry in page_items
    )
    embed.set_footer(
        text=f"Page {safe_page + 1}/{total_pages} • Showing {len(page_items)}/{len(entries)} standups"
    )
    return embed


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float | None = 300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "This panel belongs to the user who opened it.", ephemeral=True
            )
        return False


class AIActionConfirmationView(OwnedView):
    def __init__(
        self,
        *,
        owner_id: int,
        proposal_id: int,
        action_service: AIActionService,
        actor_resolver: Callable[[discord.Interaction], Actor],
        member_ids_resolver: Callable[[discord.Interaction], set[str]],
        timeout: float = 600,
    ):
        super().__init__(owner_id, timeout=timeout)
        self.proposal_id = proposal_id
        self.action_service = action_service
        self.actor_resolver = actor_resolver
        self.member_ids_resolver = member_ids_resolver

    @discord.ui.button(label="Confirm", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        try:
            result = self.action_service.confirm(
                self.actor_resolver(interaction),
                self.proposal_id,
                eligible_member_ids=self.member_ids_resolver(interaction),
            )
        except CSEHQError as error:
            await self._send_error(interaction, error)
            return
        except Exception as error:  # pragma: no cover - defensive UI boundary
            logger.exception("Unexpected AI action confirmation failure", exc_info=error)
            self._disable()
            await interaction.response.edit_message(
                content="This action could not be completed safely.",
                embed=None,
                view=self,
            )
            return
        self._disable()
        await interaction.response.edit_message(
            content=result.message,
            embed=None,
            view=self,
        )

    @discord.ui.button(label="Cancel", emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        try:
            result = self.action_service.cancel(
                self.actor_resolver(interaction), self.proposal_id
            )
        except CSEHQError as error:
            await self._send_error(interaction, error)
            return
        except Exception as error:  # pragma: no cover - defensive UI boundary
            logger.exception("Unexpected AI action cancellation failure", exc_info=error)
            await interaction.response.send_message(
                "This action could not be cancelled safely.", ephemeral=True
            )
            return
        self._disable()
        await interaction.response.edit_message(
            content=result.message,
            embed=None,
            view=self,
        )

    def _disable(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        self.stop()

    async def _send_error(
        self, interaction: discord.Interaction, error: CSEHQError
    ) -> None:
        message, terminal = _ai_action_error_message(error)
        if terminal:
            self._disable()
            await interaction.response.edit_message(
                content=message,
                embed=None,
                view=self,
            )
        else:
            await interaction.response.send_message(message, ephemeral=True)


class ProjectManageModal(discord.ui.Modal, title="Manage Project Dashboard"):
    def __init__(
        self,
        project_service: ProjectService,
        actor: Actor,
        dashboard: ProjectDashboard,
    ):
        super().__init__()
        self.project_service = project_service
        self.actor = actor
        self.goal_input = discord.ui.TextInput(
            label="Goal", max_length=256, default=dashboard.goal, required=False
        )
        self.phase_input = discord.ui.TextInput(
            label="Phase", max_length=80, default=dashboard.phase, required=False
        )
        self.sprint_input = discord.ui.TextInput(
            label="Sprint", max_length=80, default=dashboard.sprint, required=False
        )
        self.deadline_input = discord.ui.TextInput(
            label="Deadline", max_length=80, default=dashboard.deadline, required=False
        )
        self.status_input = discord.ui.TextInput(
            label="Status", max_length=80, default=dashboard.status, required=False
        )
        for item in (
            self.goal_input,
            self.phase_input,
            self.sprint_input,
            self.deadline_input,
            self.status_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.project_service.update_management(
                self.actor,
                goal=_optional(self.goal_input.value),
                phase=_optional(self.phase_input.value),
                sprint=_optional(self.sprint_input.value),
                deadline=_optional(self.deadline_input.value),
                status=_optional(self.status_input.value),
            )
            await interaction.response.send_message(
                "Dashboard settings updated. Use Refresh to load latest data.",
                ephemeral=True,
            )
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to manage this.", ephemeral=True)
        except CSEHQError as error:
            logger.exception("Project management failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to update project settings right now.", ephemeral=True
            )
        except Exception as error:  # pragma: no cover
            logger.exception("Unexpected project management error", exc_info=error)
            await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)


class TaskCreateModal(discord.ui.Modal, title="Create Task"):
    def __init__(self, task_service: TaskService, actor: Actor):
        super().__init__()
        self.task_service = task_service
        self.actor = actor
        self.title_input = discord.ui.TextInput(label="Title", max_length=120)
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        self.priority_input = discord.ui.TextInput(label="Priority (1-5)", default="3", max_length=1)
        self.assignee_input = discord.ui.TextInput(
            label="Assignee User ID", required=False, max_length=32
        )
        self.deadline_input = discord.ui.TextInput(label="Deadline", required=False, max_length=80)
        for item in (
            self.title_input,
            self.description_input,
            self.priority_input,
            self.assignee_input,
            self.deadline_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            priority = int(self.priority_input.value)
            if priority < 1 or priority > 5:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Priority must be an integer from 1 to 5.", ephemeral=True
            )
            return
        try:
            task_id = self.task_service.create_task(
                self.actor,
                title=self.title_input.value,
                description=self.description_input.value,
                priority=priority,
                assignee_id=_optional(self.assignee_input.value),
                deadline=_optional(self.deadline_input.value),
            )
            await interaction.response.send_message(
                f"Task #{task_id} created. Use Refresh to update the panel.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Task creation failed", exc_info=error)
            await interaction.response.send_message("Unable to create task right now.", ephemeral=True)
        except Exception as error:  # pragma: no cover
            logger.exception("Unexpected task create error", exc_info=error)
            await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)


class TaskFilterModal(discord.ui.Modal, title="Filter/Search Tasks"):
    def __init__(self, view: "TasksView"):
        super().__init__()
        self.tasks_view = view
        self.status_input = discord.ui.TextInput(
            label="Status (todo, in_progress, blocked, done)", required=False, max_length=20
        )
        self.priority_input = discord.ui.TextInput(label="Priority (1-5)", required=False, max_length=1)
        self.assignee_input = discord.ui.TextInput(label="Assignee User ID", required=False, max_length=32)
        self.deadline_input = discord.ui.TextInput(label="Deadline contains", required=False, max_length=80)
        self.search_input = discord.ui.TextInput(label="Search title/description", required=False, max_length=80)
        for item in (
            self.status_input,
            self.priority_input,
            self.assignee_input,
            self.deadline_input,
            self.search_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            priority = _parse_int(self.priority_input.value)
            if priority is not None and (priority < 1 or priority > 5):
                raise ValueError("Priority out of range")
            await self.tasks_view.apply_filters(
                interaction,
                status=_optional(self.status_input.value),
                priority=priority,
                assignee_id=_optional(self.assignee_input.value),
                deadline=_optional(self.deadline_input.value),
                search=_optional(self.search_input.value),
            )
        except ValueError:
            await interaction.response.send_message("Priority must be an integer from 1 to 5.", ephemeral=True)
        except Exception as error:  # pragma: no cover
            logger.exception("Task filter modal failed", exc_info=error)
            await interaction.response.send_message("Unable to apply task filters.", ephemeral=True)


class TaskEditModal(discord.ui.Modal, title="Edit Task"):
    def __init__(self, view: "TasksView", task: dict):
        super().__init__()
        self.tasks_view = view
        self.task_id = int(task["id"])
        self.title_input = discord.ui.TextInput(label="Title", default=task["title"], max_length=120)
        self.description_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            default=task["description"],
            required=False,
            max_length=1024,
        )
        self.priority_input = discord.ui.TextInput(
            label="Priority (1-5)", default=str(task["priority"]), max_length=1
        )
        self.deadline_input = discord.ui.TextInput(
            label="Deadline", default=task.get("deadline") or "", required=False, max_length=80
        )
        for item in (
            self.title_input,
            self.description_input,
            self.priority_input,
            self.deadline_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = self.tasks_view.actor_resolver(interaction)
        try:
            priority = int(self.priority_input.value)
            if priority < 1 or priority > 5:
                raise ValueError
            self.tasks_view.task_service.update_task(
                actor,
                self.task_id,
                title=self.title_input.value,
                description=self.description_input.value,
                priority=priority,
                deadline=_optional(self.deadline_input.value),
            )
            await self.tasks_view.render_detail(interaction, self.task_id, notice="Task updated.")
        except ValueError:
            await interaction.response.send_message("Priority must be an integer from 1 to 5.", ephemeral=True)
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to edit this task.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "This task no longer exists. Click Refresh to load the latest data.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Task edit failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to update this task now. Please refresh and try again.", ephemeral=True
            )


class TaskAssignModal(discord.ui.Modal, title="Assign Task"):
    def __init__(self, view: "TasksView", task_id: int):
        super().__init__()
        self.tasks_view = view
        self.task_id = task_id
        self.assignee_input = discord.ui.TextInput(
            label="Assignee User ID (blank to unassign)", required=False, max_length=32
        )
        self.add_item(self.assignee_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = self.tasks_view.actor_resolver(interaction)
        try:
            self.tasks_view.task_service.assign_task(actor, self.task_id, _optional(self.assignee_input.value))
            await self.tasks_view.render_detail(interaction, self.task_id, notice="Task assignment updated.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to assign this task.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "This task no longer exists. Click Refresh to load the latest data.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Task assign failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to assign this task now. Please refresh and try again.", ephemeral=True
            )


class BugReportModal(discord.ui.Modal, title="Report Bug"):
    def __init__(self, bug_service: BugService, actor: Actor):
        super().__init__()
        self.bug_service = bug_service
        self.actor = actor
        self.title_input = discord.ui.TextInput(label="Title", max_length=120)
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        self.severity_input = discord.ui.TextInput(label="Severity (1-5)", default="3", max_length=1)
        self.assignee_input = discord.ui.TextInput(
            label="Assignee User ID", required=False, max_length=32
        )
        for item in (
            self.title_input,
            self.description_input,
            self.severity_input,
            self.assignee_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            severity = int(self.severity_input.value)
            if severity < 1 or severity > 5:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Severity must be an integer from 1 to 5.", ephemeral=True
            )
            return
        try:
            bug_id = self.bug_service.report_bug(
                self.actor,
                title=self.title_input.value,
                description=self.description_input.value,
                severity=severity,
                assignee_id=_optional(self.assignee_input.value),
            )
            await interaction.response.send_message(
                f"Bug #{bug_id} reported. Use Refresh to update the panel.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Bug report failed", exc_info=error)
            await interaction.response.send_message("Unable to report bug right now.", ephemeral=True)
        except Exception as error:  # pragma: no cover
            logger.exception("Unexpected bug report error", exc_info=error)
            await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)


class BugFilterModal(discord.ui.Modal, title="Filter/Search Bugs"):
    def __init__(self, view: "BugsView"):
        super().__init__()
        self.bugs_view = view
        self.status_input = discord.ui.TextInput(
            label="Status (open, triaged, in_progress, resolved)", required=False, max_length=20
        )
        self.severity_input = discord.ui.TextInput(label="Severity (1-5)", required=False, max_length=1)
        self.assignee_input = discord.ui.TextInput(label="Assignee User ID", required=False, max_length=32)
        self.reporter_input = discord.ui.TextInput(label="Reporter User ID", required=False, max_length=32)
        self.search_input = discord.ui.TextInput(label="Search title/description", required=False, max_length=80)
        for item in (
            self.status_input,
            self.severity_input,
            self.assignee_input,
            self.reporter_input,
            self.search_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            severity = _parse_int(self.severity_input.value)
            if severity is not None and (severity < 1 or severity > 5):
                raise ValueError("Severity out of range")
            await self.bugs_view.apply_filters(
                interaction,
                status=_optional(self.status_input.value),
                severity=severity,
                assignee_id=_optional(self.assignee_input.value),
                reporter_id=_optional(self.reporter_input.value),
                search=_optional(self.search_input.value),
            )
        except ValueError:
            await interaction.response.send_message("Severity must be an integer from 1 to 5.", ephemeral=True)
        except Exception as error:  # pragma: no cover
            logger.exception("Bug filter modal failed", exc_info=error)
            await interaction.response.send_message("Unable to apply bug filters.", ephemeral=True)


class BugEditModal(discord.ui.Modal, title="Edit Bug"):
    def __init__(self, view: "BugsView", bug: dict):
        super().__init__()
        self.bugs_view = view
        self.bug_id = int(bug["id"])
        self.title_input = discord.ui.TextInput(label="Title", default=bug["title"], max_length=120)
        self.description_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            default=bug["description"],
            required=False,
            max_length=1024,
        )
        self.severity_input = discord.ui.TextInput(
            label="Severity (1-5)", default=str(bug["severity"]), max_length=1
        )
        for item in (self.title_input, self.description_input, self.severity_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = self.bugs_view.actor_resolver(interaction)
        try:
            severity = int(self.severity_input.value)
            if severity < 1 or severity > 5:
                raise ValueError
            self.bugs_view.bug_service.update_bug(
                actor,
                self.bug_id,
                title=self.title_input.value,
                description=self.description_input.value,
                severity=severity,
            )
            await self.bugs_view.render_detail(interaction, self.bug_id, notice="Bug updated.")
        except ValueError:
            await interaction.response.send_message("Severity must be an integer from 1 to 5.", ephemeral=True)
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to edit this bug.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "This bug no longer exists. Click Refresh to load the latest data.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Bug edit failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to update this bug now. Please refresh and try again.", ephemeral=True
            )


class BugAssignModal(discord.ui.Modal, title="Assign Bug"):
    def __init__(self, view: "BugsView", bug_id: int):
        super().__init__()
        self.bugs_view = view
        self.bug_id = bug_id
        self.assignee_input = discord.ui.TextInput(
            label="Assignee User ID (blank to unassign)", required=False, max_length=32
        )
        self.add_item(self.assignee_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = self.bugs_view.actor_resolver(interaction)
        try:
            self.bugs_view.bug_service.assign_bug(actor, self.bug_id, _optional(self.assignee_input.value))
            await self.bugs_view.render_detail(interaction, self.bug_id, notice="Bug assignment updated.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to assign this bug.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "This bug no longer exists. Click Refresh to load the latest data.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Bug assign failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to assign this bug now. Please refresh and try again.", ephemeral=True
            )


class DashboardView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        actor_resolver: Callable[[discord.Interaction], Actor],
        project_service: ProjectService,
    ):
        super().__init__(owner_id)
        self.actor_resolver = actor_resolver
        self.project_service = project_service

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        dashboard = self.project_service.get_dashboard()
        await interaction.response.edit_message(embed=build_dashboard_embed(dashboard), view=self)

    @discord.ui.button(label="Manage Dashboard", style=discord.ButtonStyle.secondary)
    async def manage(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        try:
            self.project_service.update_management(actor)
        except PermissionDeniedError:
            await interaction.response.send_message(
                "Only leaders or co-leads can manage the dashboard.", ephemeral=True
            )
            return
        dashboard = self.project_service.get_dashboard()
        await interaction.response.send_modal(ProjectManageModal(self.project_service, actor, dashboard))


class TaskSelect(discord.ui.Select):
    def __init__(self, view: "TasksView"):
        self.tasks_view = view
        super().__init__(placeholder="Select task", min_values=1, max_values=1, options=[])

    def sync_options(self, tasks: list[dict]) -> None:
        page_items, _, _ = _page_slice(tasks, self.tasks_view.page)
        options = []
        for task in page_items:
            options.append(
                discord.SelectOption(
                    label=f"#{task['id']} {_truncate(task['title'], 60)}",
                    value=str(task["id"]),
                    description=f"{_status_badge(task['status'])} • P{task['priority']}",
                )
            )
        if options:
            self.options = options
            self.disabled = False
        else:
            self.options = [discord.SelectOption(label="No tasks", value="0")]
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:
        task_id = int(self.values[0])
        await self.tasks_view.render_detail(interaction, task_id)


class TasksView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        actor_resolver: Callable[[discord.Interaction], Actor],
        task_service: TaskService,
    ):
        super().__init__(owner_id)
        self.actor_resolver = actor_resolver
        self.task_service = task_service
        self.page = 0
        self.mode = "my"
        self.filters: dict[str, object] = {}
        self.selected_task_id: int | None = None
        self.task_select = TaskSelect(self)
        self.add_item(self.task_select)
        self._sync_pagination_buttons(total_pages=1)

    def _current_filters_label(self) -> str:
        pairs = [f"{key}:{value}" for key, value in self.filters.items() if value not in (None, "")]
        return ", ".join(pairs) if pairs else "None"

    def _mode_label(self) -> str:
        return "My Tasks" if self.mode == "my" else "Accessible Tasks"

    def _load_tasks(self, actor: Actor) -> list[dict]:
        return self.task_service.filter_tasks(
            actor,
            mine_only=self.mode == "my",
            status=self.filters.get("status") if isinstance(self.filters.get("status"), str) else None,
            priority=self.filters.get("priority") if isinstance(self.filters.get("priority"), int) else None,
            assignee_id=self.filters.get("assignee_id")
            if isinstance(self.filters.get("assignee_id"), str)
            else None,
            deadline=self.filters.get("deadline") if isinstance(self.filters.get("deadline"), str) else None,
            search=self.filters.get("search") if isinstance(self.filters.get("search"), str) else None,
        )

    def _can_modify(self, actor: Actor, task: dict) -> bool:
        try:
            ensure_can_modify_task(actor, task.get("assignee_id"), task["created_by"])
            return True
        except PermissionDeniedError:
            return False

    def _sync_pagination_buttons(self, *, total_pages: int) -> None:
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= max(total_pages - 1, 0)

    async def render_list(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            tasks = self._load_tasks(actor)
            self.page, total_pages, _, _ = _pagination_state(len(tasks), self.page)
            stats = self.task_service.task_statistics(actor)
            self._sync_pagination_buttons(total_pages=total_pages)
            self.task_select.sync_options(tasks)
            embed = build_tasks_embed(
                tasks,
                page=self.page,
                mode_label=self._mode_label(),
                filters_label=self._current_filters_label(),
                stats=stats,
            )
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except CSEHQError as error:
            logger.exception("Task list refresh failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to load tasks right now. Please try Refresh.", ephemeral=True
            )

    async def render_detail(
        self, interaction: discord.Interaction, task_id: int, notice: str | None = None
    ) -> None:
        actor = self.actor_resolver(interaction)
        try:
            task = self.task_service.get_task(actor, task_id)
            can_modify = self._can_modify(actor, task)
            embed = build_task_detail_embed(task, can_modify=can_modify)
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            self.selected_task_id = task_id
            await interaction.response.edit_message(embed=embed, view=self)
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to view this task.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "Task not found. Click Refresh to load the latest list.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Task detail load failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to load task details now. Please refresh and try again.", ephemeral=True
            )

    async def apply_filters(self, interaction: discord.Interaction, **filters: object) -> None:
        try:
            status = filters.get("status")
            if isinstance(status, str) and status:
                filters["status"] = TaskStatus(status).value
        except ValueError:
            await interaction.response.send_message(
                "Invalid task status. Use: todo, in_progress, blocked, done.", ephemeral=True
            )
            return
        self.filters = filters
        self.page = 0
        await self.render_list(interaction, notice="Task filters updated.")

    @discord.ui.button(label="Create Task", style=discord.ButtonStyle.success, row=1)
    async def create_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(TaskCreateModal(self.task_service, actor))

    @discord.ui.button(label="My Tasks", style=discord.ButtonStyle.secondary, row=1)
    async def my_tasks(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.mode = "my"
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="All Tasks", style=discord.ButtonStyle.secondary, row=1)
    async def all_tasks(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.mode = "all"
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="Filter/Search", style=discord.ButtonStyle.primary, row=1)
    async def filter_search(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(TaskFilterModal(self))

    @discord.ui.button(label="Statistics", style=discord.ButtonStyle.primary, row=2)
    async def statistics(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        stats = self.task_service.task_statistics(actor)
        status_counts = stats.get("by_status", {})
        message = (
            f"Total: {stats.get('total', 0)} | My Tasks: {stats.get('my_tasks', 0)}\n"
            f"Todo: {status_counts.get(TaskStatus.TODO.value, 0)}, "
            f"In Progress: {status_counts.get(TaskStatus.IN_PROGRESS.value, 0)}, "
            f"Blocked: {status_counts.get(TaskStatus.BLOCKED.value, 0)}, "
            f"Done: {status_counts.get(TaskStatus.DONE.value, 0)}"
        )
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page = max(self.page - 1, 0)
        await self.render_list(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render_list(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=2)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.render_list(interaction, notice="Refreshed.")

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, row=3)
    async def start_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_task_id is None:
            await interaction.response.send_message("Select a task first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.task_service.start_task(actor, self.selected_task_id)
            await self.render_detail(interaction, self.selected_task_id, notice="Task moved to In Progress.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this task.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Start task failed", exc_info=error)
            await interaction.response.send_message(STALE_TASK_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Start task failed", exc_info=error)
            await interaction.response.send_message("Unable to update this task right now.", ephemeral=True)

    @discord.ui.button(label="Block", style=discord.ButtonStyle.secondary, row=3)
    async def block_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_task_id is None:
            await interaction.response.send_message("Select a task first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.task_service.block_task(actor, self.selected_task_id)
            await self.render_detail(interaction, self.selected_task_id, notice="Task marked as Blocked.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this task.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Block task failed", exc_info=error)
            await interaction.response.send_message(STALE_TASK_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Block task failed", exc_info=error)
            await interaction.response.send_message("Unable to update this task right now.", ephemeral=True)

    @discord.ui.button(label="Reopen", style=discord.ButtonStyle.secondary, row=3)
    async def reopen_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_task_id is None:
            await interaction.response.send_message("Select a task first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.task_service.reopen_task(actor, self.selected_task_id)
            await self.render_detail(interaction, self.selected_task_id, notice="Task reopened.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this task.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Reopen task failed", exc_info=error)
            await interaction.response.send_message(STALE_TASK_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Reopen task failed", exc_info=error)
            await interaction.response.send_message("Unable to update this task right now.", ephemeral=True)

    @discord.ui.button(label="Complete", style=discord.ButtonStyle.success, row=3)
    async def complete_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_task_id is None:
            await interaction.response.send_message("Select a task first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.task_service.complete_task(actor, self.selected_task_id)
            await self.render_detail(interaction, self.selected_task_id, notice="Task completed.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this task.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Complete task failed", exc_info=error)
            await interaction.response.send_message(STALE_TASK_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Complete task failed", exc_info=error)
            await interaction.response.send_message("Unable to update this task right now.", ephemeral=True)

    @discord.ui.button(label="Assign", style=discord.ButtonStyle.primary, row=4)
    async def assign_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_task_id is None:
            await interaction.response.send_message("Select a task first.", ephemeral=True)
            return
        await interaction.response.send_modal(TaskAssignModal(self, self.selected_task_id))

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=4)
    async def edit_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_task_id is None:
            await interaction.response.send_message("Select a task first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            task = self.task_service.get_task(actor, self.selected_task_id)
            await interaction.response.send_modal(TaskEditModal(self, task))
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to edit this task.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "Task not found. Click Refresh to load the latest list.", ephemeral=True
            )

    @discord.ui.button(label="Back to List", style=discord.ButtonStyle.secondary, row=4)
    async def back_to_list(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.selected_task_id = None
        await self.render_list(interaction)


def build_github_overview_embed(data: dict) -> discord.Embed:
    embed = surface_embed("github", description="### Repository pulse")
    repository = data.get("repository")
    if not repository:
        embed.description = "GitHub cache is empty. A Leader or Co-Lead can run Sync."
    else:
        embed.description = f"Repository: **{repository.get('owner')}/{repository.get('name')}**"
        embed.add_field(name="Open Issues", value=str(data.get("open_issues", 0)), inline=True)
        embed.add_field(name="Open PRs", value=str(data.get("open_pull_requests", 0)), inline=True)
        embed.add_field(name="Failing Checks", value=str(data.get("failing_checks", 0)), inline=True)
    sync = data.get("sync") or {}
    freshness = "STALE" if data.get("stale", True) else "FRESH"
    embed.add_field(
        name="Cache status",
        value=(
            f"{sync.get('status', 'NEVER_SYNCED')} • {freshness} • "
            f"Last synced: {sync.get('last_synced_at') or 'never'}"
        ),
        inline=False,
    )
    return embed


def build_github_items_embed(mode: str, items: list[dict], page: int = 0) -> discord.Embed:
    titles = {
        "issues": "🐛 Open GitHub Issues",
        "pull_requests": "🔀 Open Pull Requests",
        "commits": "🧾 Recent Commits",
        "branches": "🌿 Branches",
    }
    embed = discord.Embed(title=titles[mode], color=discord.Color.dark_teal())
    page_items, safe_page, total_pages = _page_slice(items, page)
    if not page_items:
        embed.description = "No cached records found."
    elif mode == "issues":
        embed.description = "\n".join(
            f"**#{item['number']}** {_truncate(item.get('title') or '', 70)} • {item.get('state')}\n"
            f"Assignees: {', '.join(item.get('assignees') or []) or 'none'} • "
            f"Labels: {', '.join(item.get('labels') or []) or 'none'}\n"
            f"<{item.get('url')}>"
            for item in page_items
        )
    elif mode == "pull_requests":
        embed.description = "\n".join(
            f"**#{item['number']}** {_truncate(item.get('title') or '', 65)}\n"
            f"Review: {item.get('review_status')} • Checks: {item.get('checks_status')} • "
            f"`{item.get('base_branch')}` ← `{item.get('head_branch')}`\n<{item.get('url')}>"
            for item in page_items
        )
    elif mode == "commits":
        embed.description = "\n".join(
            f"`{item.get('short_sha')}` {_truncate(item.get('message') or '', 72)} • {item.get('author')}"
            for item in page_items
        )
    else:
        embed.description = "\n".join(
            f"`{item.get('name')}` • `{item.get('latest_sha', '')[:7]}`"
            f"{' • protected' if item.get('protected') else ''}"
            for item in page_items
        )
    embed.set_footer(text=f"Cached GitHub data • Page {safe_page + 1}/{total_pages}")
    return embed


def build_github_detail_embed(mode: str, item: dict) -> discord.Embed:
    if mode == "issues":
        embed = discord.Embed(
            title=f"🐛 Issue #{item['number']} — {_truncate(item.get('title') or '', 180)}",
            color=discord.Color.dark_teal(),
            url=item.get("url"),
        )
        embed.add_field(name="State", value=item.get("state") or "UNKNOWN", inline=True)
        embed.add_field(name="Author", value=item.get("author") or "unknown", inline=True)
        embed.add_field(
            name="Assignees",
            value=", ".join(item.get("assignees") or []) or "none",
            inline=False,
        )
        embed.add_field(
            name="Labels",
            value=", ".join(item.get("labels") or []) or "none",
            inline=False,
        )
    else:
        embed = discord.Embed(
            title=f"🔀 PR #{item['number']} — {_truncate(item.get('title') or '', 180)}",
            color=discord.Color.dark_teal(),
            url=item.get("url"),
        )
        embed.add_field(name="State", value=item.get("state") or "UNKNOWN", inline=True)
        embed.add_field(name="Draft", value="Yes" if item.get("draft") else "No", inline=True)
        embed.add_field(name="Review", value=item.get("review_status") or "UNKNOWN", inline=True)
        embed.add_field(name="Checks", value=item.get("checks_status") or "UNKNOWN", inline=True)
        embed.add_field(
            name="Branches",
            value=f"`{item.get('base_branch')}` ← `{item.get('head_branch')}`",
            inline=False,
        )
    embed.set_footer(text=f"Cached GitHub data • Updated: {item.get('updated_at') or 'unknown'}")
    return embed


class GitHubLinkModal(discord.ui.Modal, title="Link CSE-HQ record to GitHub"):
    entity = discord.ui.TextInput(label="CSE-HQ record", placeholder="TASK-014 or BUG-004", max_length=20)
    external_type = discord.ui.TextInput(label="GitHub type", placeholder="issue or pull_request", max_length=20)
    external_id = discord.ui.TextInput(label="GitHub number", placeholder="23", max_length=12)

    def __init__(self, parent: "GitHubView"):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = self.parent.actor_resolver(interaction)
        try:
            prefix, raw_id = str(self.entity).strip().upper().split("-", 1)
            entity_type = {"TASK": "task", "BUG": "bug"}.get(prefix)
            if entity_type is None:
                raise InvalidInputError("Use TASK-<id> or BUG-<id>.")
            link = self.parent.github_service.create_link(
                actor,
                entity_type=entity_type,
                entity_id=int(raw_id),
                external_type=str(self.external_type),
                external_id=str(self.external_id),
            )
            await interaction.response.send_message(
                f"Linked {prefix}-{int(raw_id):03d} to GitHub {link['external_type']} #{link['external_id']}.",
                ephemeral=True,
            )
        except (CSEHQError, ValueError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)


class GitHubRecordSelect(discord.ui.Select):
    def __init__(self, parent: "GitHubView"):
        self.github_view = parent
        super().__init__(
            placeholder="Select an issue or pull request",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="No selectable records", value="0")],
            disabled=True,
            row=3,
        )

    def sync_options(self, mode: str, items: list[dict], page: int) -> None:
        if mode not in {"issues", "pull_requests"}:
            self.options = [discord.SelectOption(label="No selectable records", value="0")]
            self.disabled = True
            return
        page_items, _, _ = _page_slice(items, page)
        self.options = [
            discord.SelectOption(
                label=f"#{item['number']} {_truncate(item.get('title') or '', 80)}",
                value=str(item["number"]),
            )
            for item in page_items
        ] or [discord.SelectOption(label="No selectable records", value="0")]
        self.disabled = not page_items

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.github_view.render_detail(interaction, int(self.values[0]))


class GitHubView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        actor_resolver: Callable[[discord.Interaction], Actor],
        github_service: GitHubService,
    ):
        super().__init__(owner_id)
        self.actor_resolver = actor_resolver
        self.github_service = github_service
        self.mode = "overview"
        self.page = 0
        self.record_select = GitHubRecordSelect(self)
        self.add_item(self.record_select)

    def _items(self, actor: Actor) -> list[dict]:
        if self.mode == "issues":
            return self.github_service.list_open_issues(actor)
        if self.mode == "pull_requests":
            return self.github_service.list_open_pull_requests(actor)
        if self.mode == "commits":
            return self.github_service.list_recent_commits(actor)
        if self.mode == "branches":
            return self.github_service.list_branches(actor)
        return []

    async def _render(self, interaction: discord.Interaction) -> None:
        actor = self.actor_resolver(interaction)
        if self.mode == "overview":
            self.record_select.sync_options(self.mode, [], self.page)
            embed = build_github_overview_embed(self.github_service.get_overview(actor))
        else:
            items = self._items(actor)
            self.page, _, _, _ = _pagination_state(len(items), self.page)
            self.record_select.sync_options(self.mode, items, self.page)
            embed = build_github_items_embed(self.mode, items, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    async def render_detail(self, interaction: discord.Interaction, number: int) -> None:
        actor = self.actor_resolver(interaction)
        try:
            if self.mode == "issues":
                item = self.github_service.get_issue(actor, number)
            elif self.mode == "pull_requests":
                item = self.github_service.get_pull_request(actor, number)
            else:
                raise InvalidInputError("Select an issue or pull request first.")
            await interaction.response.edit_message(
                embed=build_github_detail_embed(self.mode, item),
                view=self,
            )
        except CSEHQError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @discord.ui.button(label="Issues", style=discord.ButtonStyle.secondary, row=0)
    async def issues(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        self.mode, self.page = "issues", 0
        await self._render(interaction)

    @discord.ui.button(label="Pull Requests", style=discord.ButtonStyle.secondary, row=0)
    async def pull_requests(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        self.mode, self.page = "pull_requests", 0
        await self._render(interaction)

    @discord.ui.button(label="Commits", style=discord.ButtonStyle.secondary, row=0)
    async def commits(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        self.mode, self.page = "commits", 0
        await self._render(interaction)

    @discord.ui.button(label="Branches", style=discord.ButtonStyle.secondary, row=0)
    async def branches(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        self.mode, self.page = "branches", 0
        await self._render(interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        self.page = max(self.page - 1, 0)
        await self._render(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        self.page += 1
        await self._render(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        self.mode, self.page = "overview", 0
        await self._render(interaction)

    @discord.ui.button(label="Sync", style=discord.ButtonStyle.primary, row=2)
    async def sync(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        actor = self.actor_resolver(interaction)
        try:
            await interaction.response.defer(ephemeral=True)
            data = await self.github_service.sync_all(actor)
            await interaction.edit_original_response(embed=build_github_overview_embed(data), view=self)
        except CSEHQError as error:
            if interaction.response.is_done():
                await interaction.followup.send(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(str(error), ephemeral=True)

    @discord.ui.button(label="Link", style=discord.ButtonStyle.secondary, row=2)
    async def link(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # type: ignore[override]
        await interaction.response.send_modal(GitHubLinkModal(self))


class BugSelect(discord.ui.Select):
    def __init__(self, view: "BugsView"):
        self.bugs_view = view
        super().__init__(placeholder="Select bug", min_values=1, max_values=1, options=[])

    def sync_options(self, bugs: list[dict]) -> None:
        page_items, _, _ = _page_slice(bugs, self.bugs_view.page)
        options = []
        for bug in page_items:
            options.append(
                discord.SelectOption(
                    label=f"#{bug['id']} {_truncate(bug['title'], 60)}",
                    value=str(bug["id"]),
                    description=f"{_status_badge(bug['status'])} • S{bug['severity']}",
                )
            )
        if options:
            self.options = options
            self.disabled = False
        else:
            self.options = [discord.SelectOption(label="No bugs", value="0")]
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:
        bug_id = int(self.values[0])
        await self.bugs_view.render_detail(interaction, bug_id)


class BugsView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        actor_resolver: Callable[[discord.Interaction], Actor],
        bug_service: BugService,
    ):
        super().__init__(owner_id)
        self.actor_resolver = actor_resolver
        self.bug_service = bug_service
        self.show_all = False
        self.page = 0
        self.filters: dict[str, object] = {}
        self.selected_bug_id: int | None = None
        self.bug_select = BugSelect(self)
        self.add_item(self.bug_select)
        self._sync_pagination_buttons(total_pages=1)

    def _current_filters_label(self) -> str:
        pairs = [f"{key}:{value}" for key, value in self.filters.items() if value not in (None, "")]
        return ", ".join(pairs) if pairs else "None"

    def _load_bugs(self, actor: Actor) -> list[dict]:
        return self.bug_service.filter_bugs(
            actor,
            open_only=not self.show_all,
            status=self.filters.get("status") if isinstance(self.filters.get("status"), str) else None,
            severity=self.filters.get("severity") if isinstance(self.filters.get("severity"), int) else None,
            assignee_id=self.filters.get("assignee_id")
            if isinstance(self.filters.get("assignee_id"), str)
            else None,
            reporter_id=self.filters.get("reporter_id")
            if isinstance(self.filters.get("reporter_id"), str)
            else None,
            search=self.filters.get("search") if isinstance(self.filters.get("search"), str) else None,
        )

    def _can_modify(self, actor: Actor, bug: dict) -> bool:
        try:
            ensure_can_modify_bug(actor, bug.get("assignee_id"), bug["created_by"])
            return True
        except PermissionDeniedError:
            return False

    def _sync_pagination_buttons(self, *, total_pages: int) -> None:
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= max(total_pages - 1, 0)

    async def render_list(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            bugs = self._load_bugs(actor)
            self.page, total_pages, _, _ = _pagination_state(len(bugs), self.page)
            stats = self.bug_service.bug_statistics(actor)
            self._sync_pagination_buttons(total_pages=total_pages)
            self.bug_select.sync_options(bugs)
            embed = build_bugs_embed(
                bugs,
                show_all=self.show_all,
                page=self.page,
                filters_label=self._current_filters_label(),
                stats=stats,
            )
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except CSEHQError as error:
            logger.exception("Bug list refresh failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to load bugs right now. Please try Refresh.", ephemeral=True
            )

    async def render_detail(self, interaction: discord.Interaction, bug_id: int, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            bug = self.bug_service.get_bug(actor, bug_id)
            can_modify = self._can_modify(actor, bug)
            embed = build_bug_detail_embed(bug, can_modify=can_modify)
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            self.selected_bug_id = bug_id
            await interaction.response.edit_message(embed=embed, view=self)
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to view this bug.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "Bug not found. Click Refresh to load the latest list.", ephemeral=True
            )
        except CSEHQError as error:
            logger.exception("Bug detail load failed", exc_info=error)
            await interaction.response.send_message(
                "Unable to load bug details now. Please refresh and try again.", ephemeral=True
            )

    async def apply_filters(self, interaction: discord.Interaction, **filters: object) -> None:
        try:
            status = filters.get("status")
            if isinstance(status, str) and status:
                filters["status"] = BugStatus(status).value
        except ValueError:
            await interaction.response.send_message(
                "Invalid bug status. Use: open, triaged, in_progress, resolved.", ephemeral=True
            )
            return
        self.filters = filters
        self.page = 0
        await self.render_list(interaction, notice="Bug filters updated.")

    @discord.ui.button(label="Report Bug", style=discord.ButtonStyle.danger, row=1)
    async def report_bug(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(BugReportModal(self.bug_service, actor))

    @discord.ui.button(label="Open Bugs", style=discord.ButtonStyle.secondary, row=1)
    async def open_bugs(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.show_all = False
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="All Bugs", style=discord.ButtonStyle.secondary, row=1)
    async def all_bugs(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.show_all = True
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="Filter/Search", style=discord.ButtonStyle.primary, row=1)
    async def filter_search(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(BugFilterModal(self))

    @discord.ui.button(label="Statistics", style=discord.ButtonStyle.primary, row=2)
    async def statistics(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        stats = self.bug_service.bug_statistics(actor)
        status_counts = stats.get("by_status", {})
        message = (
            f"Total: {stats.get('total', 0)} | Open: {stats.get('open', 0)}\n"
            f"Open: {status_counts.get(BugStatus.OPEN.value, 0)}, "
            f"Triaged: {status_counts.get(BugStatus.TRIAGED.value, 0)}, "
            f"In Progress: {status_counts.get(BugStatus.IN_PROGRESS.value, 0)}, "
            f"Resolved: {status_counts.get(BugStatus.RESOLVED.value, 0)}"
        )
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page = max(self.page - 1, 0)
        await self.render_list(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render_list(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=2)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.render_list(interaction, notice="Refreshed.")

    @discord.ui.button(label="Set In Progress", style=discord.ButtonStyle.success, row=3)
    async def mark_in_progress(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_bug_id is None:
            await interaction.response.send_message("Select a bug first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.bug_service.transition_status(actor, self.selected_bug_id, BugStatus.IN_PROGRESS.value)
            await self.render_detail(interaction, self.selected_bug_id, notice="Bug moved to In Progress.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this bug.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Bug status change failed", exc_info=error)
            await interaction.response.send_message(STALE_BUG_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Bug status change failed", exc_info=error)
            await interaction.response.send_message("Unable to update this bug right now.", ephemeral=True)

    @discord.ui.button(label="Triaged", style=discord.ButtonStyle.secondary, row=3)
    async def mark_triaged(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_bug_id is None:
            await interaction.response.send_message("Select a bug first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.bug_service.transition_status(actor, self.selected_bug_id, BugStatus.TRIAGED.value)
            await self.render_detail(interaction, self.selected_bug_id, notice="Bug marked as Triaged.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this bug.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Bug status change failed", exc_info=error)
            await interaction.response.send_message(STALE_BUG_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Bug status change failed", exc_info=error)
            await interaction.response.send_message("Unable to update this bug right now.", ephemeral=True)

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.success, row=3)
    async def resolve_bug(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_bug_id is None:
            await interaction.response.send_message("Select a bug first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.bug_service.resolve_bug(actor, self.selected_bug_id)
            await self.render_detail(interaction, self.selected_bug_id, notice="Bug resolved.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this bug.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Resolve bug failed", exc_info=error)
            await interaction.response.send_message(STALE_BUG_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Resolve bug failed", exc_info=error)
            await interaction.response.send_message("Unable to update this bug right now.", ephemeral=True)

    @discord.ui.button(label="Reopen", style=discord.ButtonStyle.secondary, row=3)
    async def reopen_bug(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_bug_id is None:
            await interaction.response.send_message("Select a bug first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.bug_service.reopen_bug(actor, self.selected_bug_id)
            await self.render_detail(interaction, self.selected_bug_id, notice="Bug reopened.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to modify this bug.", ephemeral=True)
        except (NotFoundError, InvalidTransitionError) as error:
            logger.exception("Reopen bug failed", exc_info=error)
            await interaction.response.send_message(STALE_BUG_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Reopen bug failed", exc_info=error)
            await interaction.response.send_message("Unable to update this bug right now.", ephemeral=True)

    @discord.ui.button(label="Assign", style=discord.ButtonStyle.primary, row=4)
    async def assign_bug(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_bug_id is None:
            await interaction.response.send_message("Select a bug first.", ephemeral=True)
            return
        await interaction.response.send_modal(BugAssignModal(self, self.selected_bug_id))

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=4)
    async def edit_bug(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_bug_id is None:
            await interaction.response.send_message("Select a bug first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            bug = self.bug_service.get_bug(actor, self.selected_bug_id)
            await interaction.response.send_modal(BugEditModal(self, bug))
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to edit this bug.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(
                "Bug not found. Click Refresh to load the latest list.", ephemeral=True
            )

    @discord.ui.button(label="Back to List", style=discord.ButtonStyle.secondary, row=4)
    async def back_to_list(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.selected_bug_id = None
        await self.render_list(interaction)


class MeetingCreateModal(discord.ui.Modal, title="Create Meeting"):
    def __init__(self, meeting_service: MeetingService, actor: Actor):
        super().__init__()
        self.meeting_service = meeting_service
        self.actor = actor
        self.title_input = discord.ui.TextInput(label="Title", max_length=120)
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        self.agenda_input = discord.ui.TextInput(
            label="Agenda", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        self.scheduled_input = discord.ui.TextInput(
            label="Scheduled Time (YYYY-MM-DD or ISO datetime)", max_length=80
        )
        for item in (self.title_input, self.description_input, self.agenda_input, self.scheduled_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            meeting_id = self.meeting_service.create_meeting(
                self.actor,
                self.title_input.value,
                self.description_input.value,
                self.agenda_input.value,
                self.scheduled_input.value,
            )
            await interaction.response.send_message(
                f"Meeting #{meeting_id} created. Use Refresh to update the panel.",
                ephemeral=True,
            )
        except PermissionDeniedError:
            await interaction.response.send_message("Only leaders or co-leads can create meetings.", ephemeral=True)
        except InvalidInputError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except CSEHQError as error:
            logger.exception("Meeting creation failed", exc_info=error)
            await interaction.response.send_message("Unable to create this meeting right now.", ephemeral=True)


class MeetingParticipantModal(discord.ui.Modal):
    def __init__(self, view: "MeetingsView", action_label: str):
        super().__init__(title=f"{action_label} Participant")
        self.meetings_view = view
        self.action_label = action_label
        self.user_input = discord.ui.TextInput(label="Participant User ID", max_length=32)
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.meetings_view.selected_meeting_id is None:
            await interaction.response.send_message("Select a meeting first.", ephemeral=True)
            return
        actor = self.meetings_view.actor_resolver(interaction)
        try:
            if self.action_label == "Add":
                self.meetings_view.meeting_service.add_participant(
                    actor, self.meetings_view.selected_meeting_id, self.user_input.value
                )
                notice = "Participant added."
            else:
                self.meetings_view.meeting_service.remove_participant(
                    actor, self.meetings_view.selected_meeting_id, self.user_input.value
                )
                notice = "Participant removed."
            await self.meetings_view.render_detail(interaction, self.meetings_view.selected_meeting_id, notice=notice)
        except InvalidInputError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to manage participants.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(STALE_MEETING_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Meeting participant update failed", exc_info=error)
            await interaction.response.send_message("Unable to update meeting participants right now.", ephemeral=True)


class MeetingNoteModal(discord.ui.Modal, title="Add Meeting Note"):
    def __init__(self, view: "MeetingsView"):
        super().__init__()
        self.meetings_view = view
        self.content_input = discord.ui.TextInput(
            label="Note",
            style=discord.TextStyle.paragraph,
            max_length=1024,
        )
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.meetings_view.selected_meeting_id is None:
            await interaction.response.send_message("Select a meeting first.", ephemeral=True)
            return
        actor = self.meetings_view.actor_resolver(interaction)
        try:
            self.meetings_view.meeting_service.add_note(
                actor, self.meetings_view.selected_meeting_id, self.content_input.value
            )
            await self.meetings_view.render_detail(
                interaction, self.meetings_view.selected_meeting_id, notice="Meeting note recorded."
            )
        except (InvalidInputError, PermissionDeniedError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except InvalidTransitionError:
            await interaction.response.send_message(
                "This meeting can no longer accept notes in its current state.",
                ephemeral=True,
            )
        except NotFoundError as error:
            logger.exception("Meeting note add failed", exc_info=error)
            await interaction.response.send_message(STALE_MEETING_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Meeting note add failed", exc_info=error)
            await interaction.response.send_message("Unable to add this note right now.", ephemeral=True)


class DecisionCreateModal(discord.ui.Modal, title="Record Decision"):
    def __init__(self, decision_service: DecisionService, actor: Actor, meeting_id: int | None = None):
        super().__init__()
        self.decision_service = decision_service
        self.actor = actor
        self.meeting_id = meeting_id
        self.title_input = discord.ui.TextInput(label="Title", max_length=120)
        self.decision_input = discord.ui.TextInput(
            label="Decision", style=discord.TextStyle.paragraph, max_length=1024
        )
        self.context_input = discord.ui.TextInput(
            label="Context", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        self.rationale_input = discord.ui.TextInput(
            label="Rationale", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        self.alternatives_input = discord.ui.TextInput(
            label="Alternatives", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        for item in (
            self.title_input,
            self.decision_input,
            self.context_input,
            self.rationale_input,
            self.alternatives_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            decision_id = self.decision_service.create_decision(
                self.actor,
                title=self.title_input.value,
                decision=self.decision_input.value,
                context=self.context_input.value,
                rationale=self.rationale_input.value,
                alternatives=self.alternatives_input.value,
                meeting_id=self.meeting_id,
            )
            await interaction.response.send_message(
                f"Decision #{decision_id} recorded. Use Refresh to update the panel.",
                ephemeral=True,
            )
        except PermissionDeniedError:
            await interaction.response.send_message("Only leaders or co-leads can record decisions.", ephemeral=True)
        except InvalidInputError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Decision creation failed", exc_info=error)
            await interaction.response.send_message("Unable to record this decision right now.", ephemeral=True)


class DecisionSearchModal(discord.ui.Modal, title="Search Decisions"):
    def __init__(self, view: "DecisionsView"):
        super().__init__()
        self.decisions_view = view
        self.query_input = discord.ui.TextInput(label="Search", max_length=120)
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.decisions_view.apply_search(interaction, self.query_input.value)


class DecisionEditModal(discord.ui.Modal, title="Edit Decision"):
    def __init__(self, view: "DecisionsView", decision: dict):
        super().__init__()
        self.decisions_view = view
        self.decision_id = int(decision["id"])
        self.title_input = discord.ui.TextInput(
            label="Title", default=decision.get("title") or "", max_length=120
        )
        self.decision_input = discord.ui.TextInput(
            label="Decision",
            style=discord.TextStyle.paragraph,
            default=decision.get("decision") or decision.get("summary") or "",
            max_length=1024,
        )
        self.context_input = discord.ui.TextInput(
            label="Context",
            style=discord.TextStyle.paragraph,
            default=decision.get("context") or "",
            required=False,
            max_length=1024,
        )
        self.rationale_input = discord.ui.TextInput(
            label="Rationale",
            style=discord.TextStyle.paragraph,
            default=decision.get("rationale") or "",
            required=False,
            max_length=1024,
        )
        self.alternatives_input = discord.ui.TextInput(
            label="Alternatives",
            style=discord.TextStyle.paragraph,
            default=decision.get("alternatives") or "",
            required=False,
            max_length=1024,
        )
        for item in (
            self.title_input,
            self.decision_input,
            self.context_input,
            self.rationale_input,
            self.alternatives_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = self.decisions_view.actor_resolver(interaction)
        try:
            self.decisions_view.decision_service.edit_decision(
                actor,
                self.decision_id,
                title=self.title_input.value,
                decision=self.decision_input.value,
                context=self.context_input.value,
                rationale=self.rationale_input.value,
                alternatives=self.alternatives_input.value,
            )
            await self.decisions_view.render_detail(interaction, self.decision_id, notice="Decision updated.")
        except (InvalidInputError, PermissionDeniedError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(STALE_DECISION_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Decision edit failed", exc_info=error)
            await interaction.response.send_message("Unable to update this decision right now.", ephemeral=True)


class MeetingActionTaskModal(discord.ui.Modal, title="Create Action Task"):
    def __init__(self, view: "MeetingsView"):
        super().__init__()
        self.meetings_view = view
        self.title_input = discord.ui.TextInput(label="Title", max_length=120)
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=1024
        )
        self.priority_input = discord.ui.TextInput(label="Priority (1-5)", default="3", max_length=1)
        self.assignee_input = discord.ui.TextInput(label="Assignee User ID", required=False, max_length=32)
        self.deadline_input = discord.ui.TextInput(label="Deadline", required=False, max_length=80)
        for item in (
            self.title_input,
            self.description_input,
            self.priority_input,
            self.assignee_input,
            self.deadline_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.meetings_view.selected_meeting_id is None:
            await interaction.response.send_message("Select a meeting first.", ephemeral=True)
            return
        actor = self.meetings_view.actor_resolver(interaction)
        try:
            self.meetings_view.meeting_service.get_meeting(actor, self.meetings_view.selected_meeting_id)
            priority = int(self.priority_input.value)
            if priority < 1 or priority > 5:
                raise ValueError
            task_id = self.meetings_view.task_service.create_task(
                actor,
                title=self.title_input.value,
                description=self.description_input.value,
                priority=priority,
                assignee_id=_optional(self.assignee_input.value),
                deadline=_optional(self.deadline_input.value),
                source_meeting_id=self.meetings_view.selected_meeting_id,
            )
            await interaction.response.send_message(
                f"Task #{task_id} created from this meeting.",
                ephemeral=True,
            )
        except InvalidInputError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except PermissionDeniedError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Priority must be an integer from 1 to 5.", ephemeral=True)
        except NotFoundError:
            await interaction.response.send_message(STALE_MEETING_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Meeting action task failed", exc_info=error)
            await interaction.response.send_message("Unable to create the action task right now.", ephemeral=True)


class StandupSubmitModal(discord.ui.Modal, title="Submit Standup"):
    def __init__(self, view: "StandupView", existing: dict | None):
        super().__init__()
        self.standup_view = view
        self.previous_input = discord.ui.TextInput(
            label="Previous",
            style=discord.TextStyle.paragraph,
            default=existing.get("previous", "") if existing else "",
            required=False,
            max_length=1024,
        )
        self.current_input = discord.ui.TextInput(
            label="Current",
            style=discord.TextStyle.paragraph,
            default=existing.get("current", "") if existing else "",
            max_length=1024,
        )
        self.blockers_input = discord.ui.TextInput(
            label="Blockers",
            style=discord.TextStyle.paragraph,
            default=existing.get("blockers", "") if existing else "",
            required=False,
            max_length=1024,
        )
        for item in (self.previous_input, self.current_input, self.blockers_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = self.standup_view.actor_resolver(interaction)
        try:
            self.standup_view.standup_service.submit_standup(
                actor,
                previous=self.previous_input.value,
                current=self.current_input.value,
                blockers=self.blockers_input.value,
            )
            await self.standup_view.render_list(interaction, notice="Standup saved.")
        except InvalidInputError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except CSEHQError as error:
            logger.exception("Standup submit failed", exc_info=error)
            await interaction.response.send_message("Unable to save your standup right now.", ephemeral=True)


class MeetingSelect(discord.ui.Select):
    def __init__(self, view: "MeetingsView"):
        self.meetings_view = view
        super().__init__(placeholder="Select meeting", min_values=1, max_values=1, options=[])

    def sync_options(self, meetings: list[dict]) -> None:
        page_items, _, _ = _page_slice(meetings, self.meetings_view.page)
        options = [
            discord.SelectOption(
                label=f"{_meeting_code(meeting)} {_truncate(meeting['title'], 50)}",
                value=str(meeting["id"]),
                description=f"{_status_badge(meeting['status'])} • {_truncate(meeting.get('scheduled_at') or 'N/A', 40)}",
            )
            for meeting in page_items
        ]
        if options:
            self.options = options
            self.disabled = False
        else:
            self.options = [discord.SelectOption(label="No meetings", value="0")]
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.meetings_view.render_detail(interaction, int(self.values[0]))


class MeetingsView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        actor_resolver: Callable[[discord.Interaction], Actor],
        meeting_service: MeetingService,
        decision_service: DecisionService,
        task_service: TaskService,
    ):
        super().__init__(owner_id)
        self.actor_resolver = actor_resolver
        self.meeting_service = meeting_service
        self.decision_service = decision_service
        self.task_service = task_service
        self.page = 0
        self.mode = "upcoming"
        self.selected_meeting_id: int | None = None
        self.meeting_select = MeetingSelect(self)
        self.add_item(self.meeting_select)
        self._sync_detail_buttons()

    def _load_meetings(self, actor: Actor) -> list[dict]:
        meetings = self.meeting_service.list_meetings(actor)
        if self.mode == "upcoming":
            meetings = [meeting for meeting in meetings if meeting["status"] == MeetingStatus.SCHEDULED.value]
        elif self.mode == "active":
            meetings = [meeting for meeting in meetings if meeting["status"] == MeetingStatus.IN_PROGRESS.value]
        elif self.mode == "history":
            meetings = [
                meeting
                for meeting in meetings
                if meeting["status"] in {MeetingStatus.COMPLETED.value, MeetingStatus.CANCELLED.value}
            ]
        return meetings

    def _sync_detail_buttons(
        self, meeting: dict | None = None, *, can_manage: bool = False, can_add_note: bool = False
    ) -> None:
        has_meeting = meeting is not None
        meeting_status = meeting.get("status") if meeting else None
        self.start_meeting.disabled = not (
            has_meeting and can_manage and meeting_status == MeetingStatus.SCHEDULED.value
        )
        self.complete_meeting.disabled = not (
            has_meeting and can_manage and meeting_status == MeetingStatus.IN_PROGRESS.value
        )
        self.cancel_meeting.disabled = not (
            has_meeting and can_manage and meeting_status == MeetingStatus.SCHEDULED.value
        )
        self.add_note.disabled = not (has_meeting and can_add_note)
        self.add_participant.disabled = not (has_meeting and can_manage)
        self.remove_participant.disabled = not (has_meeting and can_manage)
        self.record_decision.disabled = not (
            has_meeting and can_manage and meeting_status in {MeetingStatus.IN_PROGRESS.value, MeetingStatus.COMPLETED.value}
        )
        self.create_action_task.disabled = not has_meeting
        self.back_to_list.disabled = not has_meeting

    async def render_list(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            meetings = self._load_meetings(actor)
            self.page, _total_pages, _, _ = _pagination_state(len(meetings), self.page)
            self.meeting_select.sync_options(meetings)
            self.selected_meeting_id = None
            self._sync_detail_buttons()
            embed = build_meetings_embed(meetings, page=self.page, mode_label=self.mode.title())
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except CSEHQError as error:
            logger.exception("Meeting list refresh failed", exc_info=error)
            await interaction.response.send_message("Unable to load meetings right now. Please try Refresh.", ephemeral=True)

    async def render_detail(self, interaction: discord.Interaction, meeting_id: int, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            meeting = self.meeting_service.get_meeting(actor, meeting_id)
            participants = self.meeting_service.list_participants(actor, meeting_id)
            notes = self.meeting_service.get_notes(actor, meeting_id)
            can_manage = actor.role.value in {"leader", "co_lead"}
            can_add_note = can_manage or actor.user_id in {participant["user_id"] for participant in participants}
            self.selected_meeting_id = meeting_id
            self._sync_detail_buttons(meeting, can_manage=can_manage, can_add_note=can_add_note)
            embed = build_meeting_detail_embed(
                meeting,
                participants=participants,
                notes=notes,
                can_manage=can_manage,
                can_add_note=can_add_note,
            )
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except NotFoundError:
            meetings = self._load_meetings(actor)
            self.page, _, _, _ = _pagination_state(len(meetings), self.page)
            self.meeting_select.sync_options(meetings)
            self.selected_meeting_id = None
            self._sync_detail_buttons()
            embed = build_meetings_embed(meetings, page=self.page, mode_label=self.mode.title())
            embed.add_field(name="Info", value="Selected meeting no longer exists.", inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except CSEHQError as error:
            logger.exception("Meeting detail load failed", exc_info=error)
            await interaction.response.send_message("Unable to load meeting details now. Please refresh and try again.", ephemeral=True)

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, row=1)
    async def create_meeting(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(MeetingCreateModal(self.meeting_service, actor))

    @discord.ui.button(label="Upcoming", style=discord.ButtonStyle.secondary, row=1)
    async def show_upcoming(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.mode = "upcoming"
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="Active", style=discord.ButtonStyle.secondary, row=1)
    async def show_active(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.mode = "active"
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="History", style=discord.ButtonStyle.secondary, row=1)
    async def show_history(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.mode = "history"
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page = max(self.page - 1, 0)
        await self.render_list(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render_list(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=2)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_meeting_id is not None:
            await self.render_detail(interaction, self.selected_meeting_id, notice="Refreshed.")
            return
        await self.render_list(interaction, notice="Refreshed.")

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, row=3)
    async def start_meeting(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_meeting_id is None:
            await interaction.response.send_message("Select a meeting first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.meeting_service.start_meeting(actor, self.selected_meeting_id)
            await self.render_detail(interaction, self.selected_meeting_id, notice="Meeting started.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to start this meeting.", ephemeral=True)
        except InvalidTransitionError:
            await interaction.response.send_message(
                "This meeting cannot be started from its current state.",
                ephemeral=True,
            )
        except NotFoundError as error:
            logger.exception("Meeting start failed", exc_info=error)
            await interaction.response.send_message(STALE_MEETING_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Meeting start failed", exc_info=error)
            await interaction.response.send_message("Unable to update this meeting right now.", ephemeral=True)

    @discord.ui.button(label="Complete", style=discord.ButtonStyle.success, row=3)
    async def complete_meeting(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_meeting_id is None:
            await interaction.response.send_message("Select a meeting first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.meeting_service.complete_meeting(actor, self.selected_meeting_id)
            await self.render_detail(interaction, self.selected_meeting_id, notice="Meeting completed.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to complete this meeting.", ephemeral=True)
        except InvalidTransitionError:
            await interaction.response.send_message(
                "This meeting cannot be completed from its current state.",
                ephemeral=True,
            )
        except NotFoundError as error:
            logger.exception("Meeting complete failed", exc_info=error)
            await interaction.response.send_message(STALE_MEETING_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Meeting complete failed", exc_info=error)
            await interaction.response.send_message("Unable to update this meeting right now.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=3)
    async def cancel_meeting(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_meeting_id is None:
            await interaction.response.send_message("Select a meeting first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            self.meeting_service.cancel_meeting(actor, self.selected_meeting_id)
            await self.render_detail(interaction, self.selected_meeting_id, notice="Meeting cancelled.")
        except PermissionDeniedError:
            await interaction.response.send_message("You are not allowed to cancel this meeting.", ephemeral=True)
        except InvalidTransitionError:
            await interaction.response.send_message(
                "This meeting cannot be cancelled from its current state.",
                ephemeral=True,
            )
        except NotFoundError as error:
            logger.exception("Meeting cancel failed", exc_info=error)
            await interaction.response.send_message(STALE_MEETING_MESSAGE, ephemeral=True)
        except CSEHQError as error:
            logger.exception("Meeting cancel failed", exc_info=error)
            await interaction.response.send_message("Unable to update this meeting right now.", ephemeral=True)

    @discord.ui.button(label="Add Note", style=discord.ButtonStyle.primary, row=3)
    async def add_note(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(MeetingNoteModal(self))

    @discord.ui.button(label="Add Participant", style=discord.ButtonStyle.primary, row=4)
    async def add_participant(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(MeetingParticipantModal(self, "Add"))

    @discord.ui.button(label="Remove Participant", style=discord.ButtonStyle.secondary, row=4)
    async def remove_participant(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(MeetingParticipantModal(self, "Remove"))

    @discord.ui.button(label="Record Decision", style=discord.ButtonStyle.primary, row=4)
    async def record_decision(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(
            DecisionCreateModal(self.decision_service, actor, meeting_id=self.selected_meeting_id)
        )

    @discord.ui.button(label="Create Action Task", style=discord.ButtonStyle.primary, row=4)
    async def create_action_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(MeetingActionTaskModal(self))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=4)
    async def back_to_list(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.render_list(interaction)


class DecisionSelect(discord.ui.Select):
    def __init__(self, view: "DecisionsView"):
        self.decisions_view = view
        super().__init__(placeholder="Select decision", min_values=1, max_values=1, options=[])

    def sync_options(self, decisions: list[dict]) -> None:
        page_items, _, _ = _page_slice(decisions, self.decisions_view.page)
        options = [
            discord.SelectOption(
                label=f"{_decision_code(decision)} {_truncate(decision.get('title') or 'Untitled', 50)}",
                value=str(decision["id"]),
                description=_truncate(decision.get("decision") or decision.get("summary") or "", 70),
            )
            for decision in page_items
        ]
        if options:
            self.options = options
            self.disabled = False
        else:
            self.options = [discord.SelectOption(label="No decisions", value="0")]
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.decisions_view.render_detail(interaction, int(self.values[0]))


class DecisionsView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        actor_resolver: Callable[[discord.Interaction], Actor],
        decision_service: DecisionService,
    ):
        super().__init__(owner_id)
        self.actor_resolver = actor_resolver
        self.decision_service = decision_service
        self.page = 0
        self.mode = "browse"
        self.query: str | None = None
        self.selected_decision_id: int | None = None
        self.decision_select = DecisionSelect(self)
        self.add_item(self.decision_select)
        self.edit_decision.disabled = True
        self.back_to_list.disabled = True

    def _load_decisions(self, actor: Actor) -> list[dict]:
        if self.query:
            return self.decision_service.search_decisions(actor, self.query)
        return self.decision_service.list_decisions(actor)

    async def render_list(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            decisions = self._load_decisions(actor)
            self.page, _total_pages, _, _ = _pagination_state(len(decisions), self.page)
            self.decision_select.sync_options(decisions)
            self.selected_decision_id = None
            self.edit_decision.disabled = True
            self.back_to_list.disabled = True
            embed = build_decisions_embed(decisions, page=self.page, mode_label="Search" if self.query else "Browse")
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except InvalidInputError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
        except CSEHQError as error:
            logger.exception("Decision list refresh failed", exc_info=error)
            await interaction.response.send_message("Unable to load decisions right now. Please try Refresh.", ephemeral=True)

    async def render_detail(self, interaction: discord.Interaction, decision_id: int, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            decision = self.decision_service.get_decision(actor, decision_id)
            self.selected_decision_id = decision_id
            self.edit_decision.disabled = actor.role.value not in {"leader", "co_lead"}
            self.back_to_list.disabled = False
            embed = build_decision_detail_embed(decision, can_edit=not self.edit_decision.disabled)
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except NotFoundError:
            decisions = self._load_decisions(actor)
            self.page, _, _, _ = _pagination_state(len(decisions), self.page)
            self.decision_select.sync_options(decisions)
            self.selected_decision_id = None
            self.edit_decision.disabled = True
            self.back_to_list.disabled = True
            embed = build_decisions_embed(
                decisions, page=self.page, mode_label="Search" if self.query else "Browse"
            )
            embed.add_field(name="Info", value="Selected decision no longer exists.", inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except CSEHQError as error:
            logger.exception("Decision detail load failed", exc_info=error)
            await interaction.response.send_message("Unable to load decision details now. Please refresh and try again.", ephemeral=True)

    async def apply_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query.strip() or None
        self.page = 0
        await self.render_list(interaction, notice="Decision search updated.")

    @discord.ui.button(label="Record Decision", style=discord.ButtonStyle.success, row=1)
    async def record_new(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(DecisionCreateModal(self.decision_service, actor))

    @discord.ui.button(label="Browse", style=discord.ButtonStyle.secondary, row=1)
    async def browse(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.query = None
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, row=1)
    async def search(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(DecisionSearchModal(self))

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page = max(self.page - 1, 0)
        await self.render_list(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render_list(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=2)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_decision_id is not None:
            await self.render_detail(interaction, self.selected_decision_id, notice="Refreshed.")
            return
        await self.render_list(interaction, notice="Refreshed.")

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=3)
    async def edit_decision(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if self.selected_decision_id is None:
            await interaction.response.send_message("Select a decision first.", ephemeral=True)
            return
        actor = self.actor_resolver(interaction)
        try:
            decision = self.decision_service.get_decision(actor, self.selected_decision_id)
            await interaction.response.send_modal(DecisionEditModal(self, decision))
        except NotFoundError:
            await interaction.response.send_message(STALE_DECISION_MESSAGE, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_to_list(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.render_list(interaction)


class StandupView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        actor_resolver: Callable[[discord.Interaction], Actor],
        standup_service: StandupService,
    ):
        super().__init__(owner_id)
        self.actor_resolver = actor_resolver
        self.standup_service = standup_service
        self.page = 0
        self.mode = "team"

    async def render_list(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            today_entry = self.standup_service.get_today(actor)
            target_entries = (
                self.standup_service.list_recent(actor, days=7)
                if self.mode == "history"
                else self.standup_service.list_for_date(
                    actor,
                    today_entry["date"] if today_entry else self.standup_service.today_for_actor(actor),
                )
            )
            self.page, _, _, _ = _pagination_state(len(target_entries), self.page)
            embed = build_standup_embed(
                today_entry,
                target_entries,
                page=self.page,
                mode_label="History" if self.mode == "history" else "Today's Team",
            )
            if notice:
                embed.add_field(name="Info", value=notice, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        except CSEHQError as error:
            logger.exception("Standup list refresh failed", exc_info=error)
            await interaction.response.send_message("Unable to load standups right now. Please try Refresh.", ephemeral=True)

    @discord.ui.button(label="Submit / Update", style=discord.ButtonStyle.success, row=1)
    async def submit_update(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(StandupSubmitModal(self, self.standup_service.get_today(actor)))

    @discord.ui.button(label="Today's Team", style=discord.ButtonStyle.secondary, row=1)
    async def todays_team(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.mode = "team"
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="History", style=discord.ButtonStyle.secondary, row=1)
    async def history(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.mode = "history"
        self.page = 0
        await self.render_list(interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page = max(self.page - 1, 0)
        await self.render_list(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render_list(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=2)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.render_list(interaction, notice="Refreshed.")
