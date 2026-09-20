import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from cse_hq_bot.config import load_config
from cse_hq_bot.errors import (
    AIConfigurationError,
    AIProviderError,
    AIRateLimitError,
    AISessionBusyError,
    AISessionClosedError,
    AISessionConflictError,
    AITimeoutError,
    CSEHQError,
    InvalidInputError,
    PermissionDeniedError,
)
from cse_hq_bot.factory import ServiceContainer
from cse_hq_bot.logging_config import configure_logging
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.ui import (
    BugsView,
    DashboardView,
    DecisionsView,
    GitHubView,
    MeetingsView,
    StandupView,
    TasksView,
    build_ai_home_embed,
    build_ai_session_intro_embed,
    build_ai_sessions_embed,
    build_bugs_embed,
    build_dashboard_embed,
    build_decisions_embed,
    build_github_overview_embed,
    build_meetings_embed,
    build_standup_embed,
    build_tasks_embed,
    split_ai_response,
)

logger = logging.getLogger(__name__)


ROLE_MAP = {
    "leader": Role.LEADER,
    "co-lead": Role.CO_LEAD,
    "member": Role.MEMBER,
}


class CSEHQBot(commands.Bot):
    def __init__(self, container: ServiceContainer, *, enable_message_content: bool = False):
        intents = discord.Intents.default()
        intents.message_content = enable_message_content
        intents.guild_messages = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.container = container
        self.enable_message_content = enable_message_content
        self._message_content_guidance_sent: set[tuple[str, str]] = set()

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
                view.meeting_select.sync_options(meetings_data)
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
                    today_entry["date"] if today_entry else self.container.standup_service.today_for_actor(actor),
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

        @app_commands.command(name="ai", description="Open the private AI assistant panel")
        async def ai(interaction: discord.Interaction) -> None:
            try:
                await interaction.response.send_message(
                    embed=build_ai_home_embed(),
                    view=self._build_ai_home_view(interaction.user.id),
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("AI home command failed", exc_info=error)
                await interaction.response.send_message(
                    "Unable to open the AI assistant right now.", ephemeral=True
                )

        @app_commands.command(name="github", description="Open the read-only GitHub development panel")
        async def github(interaction: discord.Interaction) -> None:
            try:
                actor = resolve_actor_from_interaction(interaction)
                data = self.container.github_service.get_overview(actor)
                await interaction.response.send_message(
                    embed=build_github_overview_embed(data),
                    view=GitHubView(
                        owner_id=interaction.user.id,
                        actor_resolver=resolve_actor_from_interaction,
                        github_service=self.container.github_service,
                    ),
                    ephemeral=True,
                )
            except CSEHQError as error:
                await interaction.response.send_message(str(error), ephemeral=True)

        self.tree.add_command(dashboard)
        self.tree.add_command(tasks)
        self.tree.add_command(bugs)
        self.tree.add_command(weekly_report)
        self.tree.add_command(meetings)
        self.tree.add_command(decisions)
        self.tree.add_command(standup)
        self.tree.add_command(ai)
        self.tree.add_command(github)

    async def on_message(self, message: discord.Message) -> None:  # pragma: no cover - exercised via unit helpers
        if message.author.bot:
            return
        session = self.container.ai_session_service.get_session_by_thread_id(str(message.channel.id))
        if not session:
            await self.process_commands(message)
            return
        if not self.enable_message_content:
            guidance_key = (str(message.channel.id), str(message.author.id))
            if guidance_key not in self._message_content_guidance_sent:
                self._message_content_guidance_sent.add(guidance_key)
                await message.channel.send(
                    "Natural AI session chat is disabled in the current bot configuration. "
                    "Enable AI_ENABLE_MESSAGE_CONTENT and the Discord Message Content intent to use it."
                )
            return
        if not message.content.strip():
            return
        actor = resolve_actor_from_user(message.author)
        started = time.monotonic()
        try:
            typing = getattr(message.channel, "typing", None)
            if callable(typing):
                async with typing():
                    answer = await self.container.ai_session_service.handle_message(
                        actor=actor,
                        session_id=int(session["id"]),
                        discord_thread_id=str(message.channel.id),
                        content=message.content,
                    )
            else:
                answer = await self.container.ai_session_service.handle_message(
                    actor=actor,
                    session_id=int(session["id"]),
                    discord_thread_id=str(message.channel.id),
                    content=message.content,
                )
            for chunk in split_ai_response(answer.content):
                await message.channel.send(chunk)
            logger.info(
                "AI session response completed",
                extra={
                    "session_id": session["id"],
                    "actor_id": actor.user_id,
                    "retrieval_strategy": answer.retrieval_strategy,
                    "source_ids": answer.source_refs,
                    "invalid_source_ids": answer.invalid_source_refs,
                    "latency_seconds": round(time.monotonic() - started, 3),
                    "outcome": "success",
                },
            )
        except CSEHQError as error:
            logger.exception(
                "AI session message failed",
                exc_info=error,
                extra={
                    "session_id": session["id"],
                    "actor_id": actor.user_id,
                    "latency_seconds": round(time.monotonic() - started, 3),
                    "outcome": error.__class__.__name__,
                },
            )
            await message.channel.send(self._safe_ai_error_message(error))

    def _build_ai_home_view(self, owner_id: int) -> discord.ui.View:
        view = discord.ui.View(timeout=300)
        new_button = discord.ui.Button(label="New Session", style=discord.ButtonStyle.primary)
        list_button = discord.ui.Button(label="My Sessions", style=discord.ButtonStyle.secondary)

        async def ensure_owner(interaction: discord.Interaction) -> bool:
            if interaction.user.id == owner_id:
                return True
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "This panel belongs to the user who opened it.", ephemeral=True
                )
            return False

        async def create_session(interaction: discord.Interaction) -> None:
            if not await ensure_owner(interaction):
                return
            actor = resolve_actor_from_interaction(interaction)
            try:
                thread = await self._create_ai_thread(interaction)
                session = self.container.ai_session_service.create_session(actor, str(thread.id))
                await thread.send(embed=build_ai_session_intro_embed(session))
                await interaction.response.send_message(
                    f"Created AI session #{session['id']} in <#{thread.id}>.",
                    ephemeral=True,
                )
            except CSEHQError as error:
                logger.exception("AI session creation failed", exc_info=error)
                await interaction.response.send_message(self._safe_ai_error_message(error), ephemeral=True)
            except Exception as error:  # pragma: no cover
                logger.exception("Unexpected AI session creation failure", exc_info=error)
                await interaction.response.send_message(
                    "Unable to create a private AI session here.", ephemeral=True
                )

        async def list_sessions(interaction: discord.Interaction) -> None:
            if not await ensure_owner(interaction):
                return
            actor = resolve_actor_from_interaction(interaction)
            sessions = self.container.ai_session_service.list_sessions(actor)
            await interaction.response.send_message(
                embed=build_ai_sessions_embed(sessions),
                ephemeral=True,
            )

        new_button.callback = create_session
        list_button.callback = list_sessions
        view.add_item(new_button)
        view.add_item(list_button)
        return view

    async def _create_ai_thread(self, interaction: discord.Interaction):
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            if channel.type == discord.ChannelType.private_thread:
                return channel
            raise InvalidInputError("Use /ai from a standard channel or an existing private AI thread")
        if channel is None or not hasattr(channel, "create_thread"):
            raise InvalidInputError("Use /ai in a server channel that supports private threads")
        try:
            return await channel.create_thread(
                name=f"ai-session-{int(time.time())}"[:80],
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=60,
            )
        except Exception as exc:
            raise InvalidInputError("Unable to create a private AI session here") from exc

    def _safe_ai_error_message(self, error: CSEHQError) -> str:
        if isinstance(error, AISessionBusyError):
            return "This AI session is busy. Try again in a moment."
        if isinstance(error, AISessionClosedError):
            return "This AI session is closed. Start a new session from /ai."
        if isinstance(error, AISessionConflictError):
            return "This Discord thread is already linked to another AI session."
        if isinstance(error, PermissionDeniedError):
            return "You are not allowed to access this AI session."
        if isinstance(error, AIConfigurationError):
            return "AI provider configuration is unavailable right now."
        if isinstance(error, AIRateLimitError):
            return "AI provider is busy right now. Please try again later."
        if isinstance(error, AITimeoutError):
            return "AI provider timed out. Please try again."
        if isinstance(error, AIProviderError):
            return "AI provider is unavailable right now. Please try again later."
        if isinstance(error, InvalidInputError):
            return str(error)
        return "Unable to handle this AI request right now."

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:  # pragma: no cover
        logger.exception("Unhandled command error", exc_info=error)
        await ctx.send("An error occurred while executing this command.")


def resolve_actor(user_id: int, role_name: str = "member") -> Actor:
    return Actor(user_id=str(user_id), role=ROLE_MAP.get(role_name.lower(), Role.MEMBER))


def resolve_actor_from_user(user: discord.abc.User) -> Actor:
    role_name = "member"
    if isinstance(user, discord.Member):
        role_names = {role.name.lower() for role in user.roles}
        if "leader" in role_names:
            role_name = "leader"
        elif {"co-lead", "co lead", "co_lead"} & role_names:
            role_name = "co-lead"
    return resolve_actor(user.id, role_name)


def resolve_actor_from_interaction(interaction: discord.Interaction) -> Actor:
    return resolve_actor_from_user(interaction.user)


def main() -> None:  # pragma: no cover
    config = load_config()
    configure_logging(config.log_level)
    if not config.discord_token:
        raise RuntimeError("DISCORD_TOKEN is required to run the bot")
    container = ServiceContainer(config)
    bot = CSEHQBot(container, enable_message_content=config.ai_enable_message_content)
    bot.run(config.discord_token)


if __name__ == "__main__":  # pragma: no cover
    main()
