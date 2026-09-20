import logging
from collections.abc import Callable

import discord

from cse_hq_bot.errors import CSEHQError, PermissionDeniedError
from cse_hq_bot.models import Actor, ProjectDashboard
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.task_service import TaskService

logger = logging.getLogger(__name__)


def _trim(value: str, *, default: str = "N/A") -> str:
    stripped = value.strip()
    return stripped or default


def _optional(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


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


def build_tasks_embed(tasks: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="Task Management", color=discord.Color.green())
    if not tasks:
        embed.description = "No tasks yet."
        return embed
    lines = []
    for task in tasks[:10]:
        lines.append(
            f"#{task['id']} [{task['status']}] P{task['priority']} — {task['title']}"
        )
    embed.description = "\n".join(lines)
    if len(tasks) > 10:
        embed.set_footer(text=f"Showing 10/{len(tasks)} tasks")
    return embed


def build_bugs_embed(bugs: list[dict], *, show_all: bool) -> discord.Embed:
    title = "Bug Tracker (All)" if show_all else "Bug Tracker (Open)"
    embed = discord.Embed(title=title, color=discord.Color.orange())
    if not bugs:
        embed.description = "No bugs found."
        return embed
    lines = []
    for bug in bugs[:10]:
        lines.append(f"#{bug['id']} [{bug['status']}] S{bug['severity']} — {bug['title']}")
    embed.description = "\n".join(lines)
    if len(bugs) > 10:
        embed.set_footer(text=f"Showing 10/{len(bugs)} bugs")
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
            await interaction.response.send_message(f"Task #{task_id} created.", ephemeral=True)
        except CSEHQError as error:
            logger.exception("Task creation failed", exc_info=error)
            await interaction.response.send_message("Unable to create task right now.", ephemeral=True)
        except Exception as error:  # pragma: no cover
            logger.exception("Unexpected task create error", exc_info=error)
            await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)


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
            await interaction.response.send_message(f"Bug #{bug_id} reported.", ephemeral=True)
        except CSEHQError as error:
            logger.exception("Bug report failed", exc_info=error)
            await interaction.response.send_message("Unable to report bug right now.", ephemeral=True)
        except Exception as error:  # pragma: no cover
            logger.exception("Unexpected bug report error", exc_info=error)
            await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)


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

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        tasks = self.task_service.list_tasks()
        await interaction.response.edit_message(embed=build_tasks_embed(tasks), view=self)

    @discord.ui.button(label="Create Task", style=discord.ButtonStyle.success)
    async def create_task(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(TaskCreateModal(self.task_service, actor))


class BugFilterSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Open Bugs", value="open", default=True),
            discord.SelectOption(label="All Bugs", value="all"),
        ]
        super().__init__(placeholder="Filter Bugs", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        view = self.view
        if isinstance(view, BugsView):
            view.show_all = self.values[0] == "all"
            await view.refresh(interaction, None)


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
        self.add_item(BugFilterSelect())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button | None
    ) -> None:
        bugs = self.bug_service.list_bugs()
        if not self.show_all:
            bugs = [bug for bug in bugs if bug["status"] != "resolved"]
        await interaction.response.edit_message(
            embed=build_bugs_embed(bugs, show_all=self.show_all), view=self
        )

    @discord.ui.button(label="Report Bug", style=discord.ButtonStyle.danger)
    async def report_bug(  # type: ignore[override]
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        actor = self.actor_resolver(interaction)
        await interaction.response.send_modal(BugReportModal(self.bug_service, actor))
