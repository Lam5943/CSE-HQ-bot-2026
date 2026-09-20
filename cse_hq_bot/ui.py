import logging
from collections.abc import Callable

import discord

from cse_hq_bot.errors import CSEHQError, NotFoundError, PermissionDeniedError
from cse_hq_bot.models import Actor, BugStatus, ProjectDashboard, TaskStatus
from cse_hq_bot.permissions import ensure_can_modify_bug, ensure_can_modify_task
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.task_service import TaskService

logger = logging.getLogger(__name__)

PAGE_SIZE = 8


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
    if not items:
        return [], 0, 1
    max_page = max((len(items) - 1) // page_size, 0)
    safe_page = max(0, min(page, max_page))
    start = safe_page * page_size
    end = start + page_size
    return items[start:end], safe_page, max_page + 1


def _status_badge(status: str) -> str:
    return status.replace("_", " ").title()


def build_dashboard_embed(dashboard: ProjectDashboard) -> discord.Embed:
    embed = discord.Embed(
        title=f"{dashboard.name} Dashboard",
        description=dashboard.description,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Goal", value=_trim(dashboard.goal), inline=True)
    embed.add_field(name="Phase", value=_trim(dashboard.phase), inline=True)
    embed.add_field(name="Sprint", value=_trim(dashboard.sprint), inline=True)
    embed.add_field(name="Deadline", value=_trim(dashboard.deadline), inline=True)
    embed.add_field(name="Status", value=_trim(dashboard.status), inline=True)
    embed.add_field(name="Open Tasks", value=str(dashboard.task_open), inline=True)
    embed.add_field(name="Completed Tasks", value=str(dashboard.task_done), inline=True)
    embed.add_field(name="Open Bugs", value=str(dashboard.bug_open), inline=True)
    embed.add_field(name="Meetings", value=str(dashboard.meetings_total), inline=True)
    embed.set_footer(text=f"Updated: {dashboard.updated_at.isoformat(sep=' ', timespec='seconds')}")
    return embed


def build_tasks_embed(
    tasks: list[dict],
    *,
    page: int = 0,
    mode_label: str = "All Tasks",
    filters_label: str = "None",
    stats: dict | None = None,
) -> discord.Embed:
    embed = discord.Embed(title="Task Management", color=discord.Color.green())
    stats = stats or {}
    embed.add_field(name="Scope", value=mode_label, inline=True)
    embed.add_field(name="Filters", value=filters_label, inline=True)
    embed.add_field(name="Total", value=str(len(tasks)), inline=True)
    if stats:
        embed.add_field(name="My Tasks", value=str(stats.get("my_tasks", 0)), inline=True)
        embed.add_field(name="High Priority", value=str(stats.get("high_priority", 0)), inline=True)
        embed.add_field(name="With Deadline", value=str(stats.get("with_deadline", 0)), inline=True)
    if not tasks:
        embed.description = "No tasks found."
        return embed
    page_items, safe_page, total_pages = _page_slice(tasks, page)
    lines = [
        f"`#{task['id']}` [{_status_badge(task['status'])}] P{task['priority']} — {_truncate(task['title'])}"
        for task in page_items
    ]
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Page {safe_page + 1}/{total_pages} • Showing {len(page_items)}/{len(tasks)} tasks")
    return embed


def build_task_detail_embed(task: dict, *, can_modify: bool) -> discord.Embed:
    embed = discord.Embed(
        title=f"Task #{task['id']} — {_truncate(task['title'], 120)}",
        description=task["description"] or "No description.",
        color=discord.Color.teal(),
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
    title = "Bug Tracker (All)" if show_all else "Bug Tracker (Open)"
    embed = discord.Embed(title=title, color=discord.Color.orange())
    stats = stats or {}
    embed.add_field(name="Filters", value=filters_label, inline=True)
    embed.add_field(name="Total", value=str(len(bugs)), inline=True)
    embed.add_field(name="Open", value=str(stats.get("open", 0)), inline=True)
    if stats:
        embed.add_field(name="Critical", value=str(stats.get("critical", 0)), inline=True)
        embed.add_field(name="Unassigned", value=str(stats.get("unassigned", 0)), inline=True)
        embed.add_field(
            name="Resolved",
            value=str(stats.get("by_status", {}).get(BugStatus.RESOLVED.value, 0)),
            inline=True,
        )
    if not bugs:
        embed.description = "No bugs found."
        return embed
    page_items, safe_page, total_pages = _page_slice(bugs, page)
    lines = [
        f"`#{bug['id']}` [{_status_badge(bug['status'])}] S{bug['severity']} — {_truncate(bug['title'])}"
        for bug in page_items
    ]
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Page {safe_page + 1}/{total_pages} • Showing {len(page_items)}/{len(bugs)} bugs")
    return embed


def build_bug_detail_embed(bug: dict, *, can_modify: bool) -> discord.Embed:
    embed = discord.Embed(
        title=f"Bug #{bug['id']} — {_truncate(bug['title'], 120)}",
        description=bug["description"] or "No description.",
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


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "This panel belongs to the user who opened it.", ephemeral=True
            )
        return False


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

    async def render_list(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            tasks = self._load_tasks(actor)
            stats = self.task_service.task_statistics(actor)
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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Start task failed", exc_info=error)
            await interaction.response.send_message(
                "Task action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Block task failed", exc_info=error)
            await interaction.response.send_message(
                "Task action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Reopen task failed", exc_info=error)
            await interaction.response.send_message(
                "Task action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Complete task failed", exc_info=error)
            await interaction.response.send_message(
                "Task action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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

    async def render_list(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        actor = self.actor_resolver(interaction)
        try:
            bugs = self._load_bugs(actor)
            stats = self.bug_service.bug_statistics(actor)
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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Bug status change failed", exc_info=error)
            await interaction.response.send_message(
                "Bug action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Bug status change failed", exc_info=error)
            await interaction.response.send_message(
                "Bug action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Resolve bug failed", exc_info=error)
            await interaction.response.send_message(
                "Bug action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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
        except (NotFoundError, CSEHQError) as error:
            logger.exception("Reopen bug failed", exc_info=error)
            await interaction.response.send_message(
                "Bug action is no longer valid. Please refresh the list and try again.", ephemeral=True
            )

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
