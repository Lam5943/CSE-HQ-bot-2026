import logging
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from cse_hq_bot.config import load_config
from cse_hq_bot.errors import CSEHQError
from cse_hq_bot.factory import ServiceContainer
from cse_hq_bot.logging_config import configure_logging
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.ui import (
    BugsView,
    DecisionsView,
    DashboardView,
    MeetingsView,
    StandupView,
    TasksView,
    build_bugs_embed,
    build_decisions_embed,
    build_dashboard_embed,
    build_meetings_embed,
    build_standup_embed,
    build_tasks_embed,
)

logger = logging.getLogger(__name__)


ROLE_MAP = {
    "leader": Role.LEADER,
    "co-lead": Role.CO_LEAD,
    "member": Role.MEMBER,
}


class CSEHQBot(commands.Bot):
    def __init__(self, container: ServiceContainer):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.container = container

    async def setup_hook(self) -> None:
        @app_commands.command(name="dashboard", description="Show project dashboard")
        async def dashboard(interaction: discord.Interaction) -> None:
            try:
                data = self.container.project_service.get_dashboard()
                view = DashboardView(
                    owner_id=interaction.user.id,
                    actor_resolver=resolve_actor_from_interaction,
                    project_service=self.container.project_service,
                )
                await interaction.response.send_message(
                    embed=build_dashboard_embed(data),
                    view=view,
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("Dashboard command failed", exc_info=error)
                await interaction.response.send_message(
                    "Unable to load dashboard right now.", ephemeral=True
                )
            except Exception as error:  # pragma: no cover
                logger.exception("Unexpected dashboard command failure", exc_info=error)
                await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)

        @app_commands.command(name="tasks", description="Open task management panel")
        async def tasks(interaction: discord.Interaction) -> None:
            try:
                actor = resolve_actor_from_interaction(interaction)
                data = self.container.task_service.list_my_tasks(actor)
                view = TasksView(
                    owner_id=interaction.user.id,
                    actor_resolver=resolve_actor_from_interaction,
                    task_service=self.container.task_service,
                )
                await interaction.response.send_message(
                    embed=build_tasks_embed(
                        data,
                        mode_label="My Tasks",
                        stats=self.container.task_service.task_statistics(actor),
                    ),
                    view=view,
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("Tasks command failed", exc_info=error)
                await interaction.response.send_message(
                    "Unable to load tasks right now.", ephemeral=True
                )
            except Exception as error:  # pragma: no cover
                logger.exception("Unexpected tasks command failure", exc_info=error)
                await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)

        @app_commands.command(name="bugs", description="Open bug tracking panel")
        async def bugs(interaction: discord.Interaction) -> None:
            try:
                actor = resolve_actor_from_interaction(interaction)
                open_bugs = self.container.bug_service.list_open_bugs(actor)
                view = BugsView(
                    owner_id=interaction.user.id,
                    actor_resolver=resolve_actor_from_interaction,
                    bug_service=self.container.bug_service,
                )
                await interaction.response.send_message(
                    embed=build_bugs_embed(
                        open_bugs,
                        show_all=False,
                        stats=self.container.bug_service.bug_statistics(actor),
                    ),
                    view=view,
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("Bugs command failed", exc_info=error)
                await interaction.response.send_message(
                    "Unable to load bugs right now.", ephemeral=True
                )
            except Exception as error:  # pragma: no cover
                logger.exception("Unexpected bugs command failure", exc_info=error)
                await interaction.response.send_message("Unexpected error occurred.", ephemeral=True)

        @app_commands.command(name="weekly_report", description="Show weekly report")
        async def weekly_report(interaction: discord.Interaction) -> None:
            report = self.container.report_service.weekly_progress_report()
            await interaction.response.send_message(report, ephemeral=True)

        @app_commands.command(name="meetings", description="Open meetings panel")
        async def meetings(interaction: discord.Interaction) -> None:
            try:
                actor = resolve_actor_from_interaction(interaction)
                meetings_data = self.container.meeting_service.list_meetings(actor, status="scheduled")
                view = MeetingsView(
                    owner_id=interaction.user.id,
                    actor_resolver=resolve_actor_from_interaction,
                    meeting_service=self.container.meeting_service,
                    decision_service=self.container.decision_service,
                    task_service=self.container.task_service,
                )
                await interaction.response.send_message(
                    embed=build_meetings_embed(meetings_data, mode_label="Upcoming"),
                    view=view,
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("Meetings command failed", exc_info=error)
                await interaction.response.send_message(
                    "Unable to load meetings right now.", ephemeral=True
                )

        @app_commands.command(name="decisions", description="Open decisions panel")
        async def decisions(interaction: discord.Interaction) -> None:
            try:
                actor = resolve_actor_from_interaction(interaction)
                decisions_data = self.container.decision_service.list_decisions(actor)
                view = DecisionsView(
                    owner_id=interaction.user.id,
                    actor_resolver=resolve_actor_from_interaction,
                    decision_service=self.container.decision_service,
                )
                await interaction.response.send_message(
                    embed=build_decisions_embed(decisions_data),
                    view=view,
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("Decisions command failed", exc_info=error)
                await interaction.response.send_message(
                    "Unable to load decisions right now.", ephemeral=True
                )

        @app_commands.command(name="standup", description="Open standup panel")
        async def standup(interaction: discord.Interaction) -> None:
            try:
                actor = resolve_actor_from_interaction(interaction)
                today_entry = self.container.standup_service.get_today(actor)
                entries = self.container.standup_service.list_for_date(
                    actor,
                    today_entry["date"] if today_entry else date.today().isoformat(),
                )
                view = StandupView(
                    owner_id=interaction.user.id,
                    actor_resolver=resolve_actor_from_interaction,
                    standup_service=self.container.standup_service,
                )
                await interaction.response.send_message(
                    embed=build_standup_embed(today_entry, entries),
                    view=view,
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("Standup command failed", exc_info=error)
                await interaction.response.send_message(
                    "Unable to load standups right now.", ephemeral=True
                )

        self.tree.add_command(dashboard)
        self.tree.add_command(tasks)
        self.tree.add_command(bugs)
        self.tree.add_command(weekly_report)
        self.tree.add_command(meetings)
        self.tree.add_command(decisions)
        self.tree.add_command(standup)

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:  # pragma: no cover
        logger.exception("Unhandled command error", exc_info=error)
        await ctx.send("An error occurred while executing this command.")


def resolve_actor(user_id: int, role_name: str = "member") -> Actor:
    return Actor(user_id=str(user_id), role=ROLE_MAP.get(role_name.lower(), Role.MEMBER))


def resolve_actor_from_interaction(interaction: discord.Interaction) -> Actor:
    role_name = "member"
    if isinstance(interaction.user, discord.Member):
        role_names = {role.name.lower() for role in interaction.user.roles}
        if "leader" in role_names:
            role_name = "leader"
        elif {"co-lead", "co lead", "co_lead"} & role_names:
            role_name = "co-lead"
    return resolve_actor(interaction.user.id, role_name)


def main() -> None:  # pragma: no cover
    config = load_config()
    configure_logging(config.log_level)
    if not config.discord_token:
        raise RuntimeError("DISCORD_TOKEN is required to run the bot")
    container = ServiceContainer(config)
    bot = CSEHQBot(container)
    bot.run(config.discord_token)


if __name__ == "__main__":  # pragma: no cover
    main()
