import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from cse_hq_bot.ai.action_models import KnownMember
from cse_hq_bot.ai.discord_image_input import extract_ai_images
from cse_hq_bot.config import load_config
from cse_hq_bot.discord_forum_gateway import DiscordForumGateway
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
    AIActionConfirmationView,
    BugsView,
    DashboardView,
    DecisionsView,
    GitHubView,
    MeetingsView,
    StandupView,
    TasksView,
    build_ai_action_embed,
    build_ai_home_embed,
    build_ai_session_intro_embed,
    build_ai_sessions_embed,
    build_bugs_embed,
    build_dashboard_embed,
    build_decisions_embed,
    build_github_overview_embed,
    build_health_embed,
    build_meetings_embed,
    build_standup_embed,
    build_tasks_embed,
    build_weekly_dashboard_embed,
    split_ai_response,
)
from cse_hq_bot.version import application_version

logger = logging.getLogger(__name__)


ROLE_MAP = {
    "leader": Role.LEADER,
    "co-lead": Role.CO_LEAD,
    "member": Role.MEMBER,
}


def build_ai_session_thread_name(display_name: str, session_number: int) -> str:
    prefix = "session của "
    suffix = f" #{max(1, int(session_number))}"
    clean_name = " ".join(str(display_name or "user").split()) or "user"
    available = max(1, 80 - len(prefix) - len(suffix))
    clean_name = clean_name[:available].rstrip() or "user"
    return f"{prefix}{clean_name}{suffix}"


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
        self._weekly_dashboard_task: asyncio.Task[None] | None = None
        self._ai_session_creation_locks: dict[str, asyncio.Lock] = {}
        forum_service = getattr(self.container, "forum_publishing_service", None)
        if forum_service is not None:
            forum_service.set_gateway(DiscordForumGateway(self))

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

        @app_commands.command(
            name="health",
            description="Show bounded operational diagnostics for maintainers",
        )
        async def health(interaction: discord.Interaction) -> None:
            actor = resolve_actor_from_interaction(interaction)
            try:
                report = self.container.health_service.get_report(
                    actor, discord_ready=self.is_ready()
                )
                await interaction.response.send_message(
                    embed=build_health_embed(report), ephemeral=True
                )
            except CSEHQError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
            except Exception as error:  # pragma: no cover - defensive command boundary
                logger.exception(
                    "Health command failed",
                    exc_info=error,
                    extra={
                        "component": "health",
                        "operation": "discord_command",
                        "result": "failed",
                        "error_category": error.__class__.__name__,
                    },
                )
                await interaction.response.send_message(
                    "Unable to load health diagnostics right now.", ephemeral=True
                )

        setup = app_commands.Group(
            name="setup", description="Configure CSE-HQ integrations"
        )

        @setup.command(
            name="dashboard",
            description="Configure the public weekly dashboard channel and schedule",
        )
        @app_commands.describe(
            channel="Text channel where weekly dashboards are published",
            weekday="Publish weekday in English, e.g. Monday",
            publish_time="24-hour local time in HH:MM format",
        )
        async def setup_dashboard(
            interaction: discord.Interaction,
            channel: discord.TextChannel,
            weekday: str = "monday",
            publish_time: str = "09:00",
        ) -> None:
            actor = resolve_actor_from_interaction(interaction)
            try:
                guild = interaction.guild
                bot_member = guild.me if guild is not None else None
                if bot_member is None:
                    raise InvalidInputError("Weekly dashboard setup must be used in a server")
                permissions = channel.permissions_for(bot_member)
                if not permissions.view_channel or not permissions.send_messages:
                    raise InvalidInputError(
                        "CSE-HQ needs View Channel and Send Messages permissions in that channel"
                    )
                settings = self.container.weekly_dashboard_service.configure(
                    actor,
                    channel_id=str(channel.id),
                    weekday=weekday,
                    publish_time=publish_time,
                )
                weekday_name = (
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                )[int(settings["weekday"])]
                await interaction.response.send_message(
                    (
                        f"Weekly dashboard configured for {channel.mention}: "
                        f"{weekday_name} at {settings['publish_time']} "
                        f"({settings['timezone']})."
                    ),
                    ephemeral=True,
                )
            except CSEHQError as error:
                await interaction.response.send_message(str(error), ephemeral=True)

        @setup.command(
            name="forums",
            description="Configure existing Forum channels for automatic publishing",
        )
        @app_commands.describe(
            bugs="Forum channel for internal and labeled GitHub bugs",
            pull_requests="Forum channel for GitHub pull requests",
            releases="Forum channel for published GitHub releases",
            test_publish="Create one harmless test post in each configured Forum",
        )
        async def setup_forums(
            interaction: discord.Interaction,
            bugs: discord.ForumChannel,
            pull_requests: discord.ForumChannel,
            releases: discord.ForumChannel,
            test_publish: bool = False,
        ) -> None:
            actor = resolve_actor_from_interaction(interaction)
            try:
                await self.container.forum_publishing_service.configure_forums(
                    actor,
                    {
                        "bug": str(bugs.id),
                        "pull_request": str(pull_requests.id),
                        "release": str(releases.id),
                    },
                )
                test_threads: list[str] = []
                if test_publish:
                    for forum_kind in ("bug", "pull_request", "release"):
                        test_threads.append(
                            await self.container.forum_publishing_service.test_publish(
                                actor, forum_kind
                            )
                        )
                message = "Forum publishing configuration saved."
                if test_threads:
                    message += " Test posts: " + ", ".join(
                        f"<#{thread_id}>" for thread_id in test_threads
                    )
                await interaction.response.send_message(message, ephemeral=True)
            except CSEHQError as error:
                await interaction.response.send_message(str(error), ephemeral=True)

        @app_commands.command(
            name="weekly_dashboard",
            description="Publish this week's dashboard snapshot to the configured team channel",
        )
        async def weekly_dashboard(interaction: discord.Interaction) -> None:
            actor = resolve_actor_from_interaction(interaction)
            try:
                payload = self.container.weekly_dashboard_service.prepare_manual(actor)
                await interaction.response.defer(ephemeral=True)
                message = await self._publish_weekly_dashboard(payload)
                await interaction.followup.send(
                    f"Published {payload['week_key']} weekly dashboard: {message.jump_url}",
                    ephemeral=True,
                )
            except CSEHQError as error:
                if interaction.response.is_done():
                    await interaction.followup.send(str(error), ephemeral=True)
                else:
                    await interaction.response.send_message(str(error), ephemeral=True)
            except Exception as error:  # pragma: no cover - Discord transport boundary
                logger.exception("Weekly dashboard publication failed", exc_info=error)
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "Unable to publish the weekly dashboard right now.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "Unable to publish the weekly dashboard right now.",
                        ephemeral=True,
                    )

        self.tree.add_command(dashboard)
        self.tree.add_command(tasks)
        self.tree.add_command(bugs)
        self.tree.add_command(weekly_report)
        self.tree.add_command(meetings)
        self.tree.add_command(decisions)
        self.tree.add_command(standup)
        self.tree.add_command(ai)
        self.tree.add_command(github)
        self.tree.add_command(health)
        self.tree.add_command(weekly_dashboard)
        self.tree.add_command(setup)

        if self.application_id is not None:
            synced = await self.tree.sync()
            logger.info(
                "Application commands synced",
                extra={
                    "component": "discord",
                    "operation": "command_sync",
                    "result": "success",
                    "command_count": len(synced),
                },
            )
            if self._weekly_dashboard_task is None:
                self._weekly_dashboard_task = asyncio.create_task(
                    self._weekly_dashboard_loop()
                )

        webhook_server = getattr(self.container, "github_webhook_server", None)
        if webhook_server is not None:
            await webhook_server.start()

    async def _publish_weekly_dashboard(self, payload: dict) -> discord.Message:
        channel_id = int(payload["channel_id"])
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise InvalidInputError(
                "Configured weekly dashboard channel is not a text channel"
            )
        guild = channel.guild
        bot_member = guild.me
        if bot_member is None:
            raise InvalidInputError("Unable to resolve the bot member in this server")
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.send_messages:
            raise InvalidInputError(
                "CSE-HQ cannot send messages in the configured weekly dashboard channel"
            )

        message = await channel.send(
            embed=build_weekly_dashboard_embed(
                payload["dashboard"],
                payload["week_key"],
            )
        )
        recorded = self.container.weekly_dashboard_service.record_publication(
            week_key=payload["week_key"],
            channel_id=str(channel.id),
            message_id=str(message.id),
            snapshot_json=payload["snapshot_json"],
        )
        if not recorded:
            try:
                await message.delete()
            except discord.HTTPException:
                logger.warning(
                    "Duplicate weekly dashboard message could not be deleted",
                    extra={
                        "component": "weekly_dashboard",
                        "operation": "dedupe_cleanup",
                        "result": "failed",
                        "week_key": payload["week_key"],
                    },
                )
            raise InvalidInputError(
                f"Weekly dashboard {payload['week_key']} has already been published"
            )
        return message

    async def _weekly_dashboard_loop(self) -> None:
        while not self.is_closed():
            try:
                payload = self.container.weekly_dashboard_service.prepare_due()
                if payload is not None:
                    message = await self._publish_weekly_dashboard(payload)
                    logger.info(
                        "Weekly dashboard published",
                        extra={
                            "component": "weekly_dashboard",
                            "operation": "scheduled_publish",
                            "result": "success",
                            "week_key": payload["week_key"],
                            "channel_id": payload["channel_id"],
                            "message_id": str(message.id),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception(
                    "Scheduled weekly dashboard publication failed",
                    exc_info=error,
                    extra={
                        "component": "weekly_dashboard",
                        "operation": "scheduled_publish",
                        "result": "failed",
                        "error_category": error.__class__.__name__,
                    },
                )
            await asyncio.sleep(60)

    async def close(self) -> None:
        if self._weekly_dashboard_task is not None:
            self._weekly_dashboard_task.cancel()
            try:
                await self._weekly_dashboard_task
            except asyncio.CancelledError:
                pass
            self._weekly_dashboard_task = None
        webhook_server = getattr(self.container, "github_webhook_server", None)
        if webhook_server is not None:
            await webhook_server.stop()
        forum_service = getattr(self.container, "forum_publishing_service", None)
        if forum_service is not None:
            await forum_service.close()
        await super().close()

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
        attachments = list(getattr(message, "attachments", []) or [])
        if not message.content.strip() and not attachments:
            return
        actor = resolve_actor_from_user(message.author)
        known_members = known_members_from_message(message)
        started = time.monotonic()
        try:
            images = await extract_ai_images(attachments)
            request_kwargs = {
                "actor": actor,
                "session_id": int(session["id"]),
                "discord_thread_id": str(message.channel.id),
                "content": message.content,
                "known_members": known_members,
            }
            if images:
                request_kwargs["images"] = images

            typing = getattr(message.channel, "typing", None)
            if callable(typing):
                async with typing():
                    answer = await self.container.ai_session_service.handle_message(
                        **request_kwargs
                    )
            else:
                answer = await self.container.ai_session_service.handle_message(
                    **request_kwargs
                )
            if answer.action_proposal is not None:
                view = AIActionConfirmationView(
                    owner_id=message.author.id,
                    proposal_id=answer.action_proposal.id,
                    action_service=self.container.ai_action_service,
                    actor_resolver=resolve_actor_from_interaction,
                    member_ids_resolver=known_member_ids_from_interaction,
                    timeout=max(
                        60,
                        self.container.ai_action_service.expiration_seconds,
                    ),
                )
                await message.channel.send(
                    embed=build_ai_action_embed(answer.action_proposal),
                    view=view,
                )
            else:
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
                    "image_count": len(images),
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
                lock = self._ai_session_creation_locks.setdefault(
                    actor.user_id,
                    asyncio.Lock(),
                )
                async with lock:
                    session_number = (
                        self.container.ai_session_service.next_session_number(actor)
                    )
                    thread = await self._create_ai_thread(
                        interaction,
                        session_number=session_number,
                    )
                    session = self.container.ai_session_service.create_session(
                        actor,
                        str(thread.id),
                    )
                await thread.send(embed=build_ai_session_intro_embed(session))
                await interaction.response.send_message(
                    f"Created your AI session in <#{thread.id}>.",
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

    async def _create_ai_thread(
        self,
        interaction: discord.Interaction,
        *,
        session_number: int,
    ):
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            if channel.type == discord.ChannelType.private_thread:
                return channel
            raise InvalidInputError("Use /ai from a standard channel or an existing private AI thread")
        if channel is None or not hasattr(channel, "create_thread"):
            raise InvalidInputError("Use /ai in a server channel that supports private threads")
        try:
            display_name = (
                getattr(interaction.user, "display_name", None)
                or getattr(interaction.user, "name", None)
                or "user"
            )
            return await channel.create_thread(
                name=build_ai_session_thread_name(display_name, session_number),
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


def known_members_from_message(message: discord.Message) -> list[KnownMember]:
    candidates = [message.author]
    candidates.extend(getattr(message, "mentions", []) or [])
    guild = getattr(message, "guild", None)
    if guild is not None:
        candidates.extend(getattr(guild, "members", []) or [])
    members: dict[str, KnownMember] = {}
    for user in candidates:
        if getattr(user, "bot", False):
            continue
        user_id = str(user.id)
        members[user_id] = KnownMember(
            user_id=user_id,
            display_name=str(
                getattr(user, "display_name", None)
                or getattr(user, "name", None)
                or user_id
            ),
            username=str(getattr(user, "name", "") or "") or None,
        )
    return list(members.values())


def resolve_actor_from_interaction(interaction: discord.Interaction) -> Actor:
    return resolve_actor_from_user(interaction.user)


def known_member_ids_from_interaction(
    interaction: discord.Interaction,
) -> set[str]:
    member_ids = {str(interaction.user.id)}
    guild = interaction.guild
    if guild is not None:
        member_ids.update(
            str(member.id)
            for member in getattr(guild, "members", [])
            if not getattr(member, "bot", False)
        )
    return member_ids


def main() -> None:  # pragma: no cover
    config = load_config()
    configure_logging(config.log_level)
    logger.info(
        "Starting CSE-HQ",
        extra={
            "component": "application",
            "operation": "startup",
            "result": "starting",
            "application_version": application_version(),
        },
    )
    if not config.discord_token:
        raise RuntimeError("DISCORD_TOKEN is required to run the bot")
    container = ServiceContainer(config)
    bot = CSEHQBot(container, enable_message_content=config.ai_enable_message_content)
    bot.run(config.discord_token)


if __name__ == "__main__":  # pragma: no cover
    main()
