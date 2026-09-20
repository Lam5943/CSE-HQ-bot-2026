import logging

import discord
from discord import app_commands
from discord.ext import commands

from cse_hq_bot.config import load_config
from cse_hq_bot.factory import ServiceContainer
from cse_hq_bot.logging_config import configure_logging
from cse_hq_bot.models import Actor, Role

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
            data = self.container.project_service.get_dashboard()
            await interaction.response.send_message(
                (
                    f"{data.name}\n{data.description}\n"
                    f"Tasks open: {data.task_open} | Bugs open: {data.bug_open}"
                ),
                ephemeral=True,
            )

        @app_commands.command(name="weekly_report", description="Show weekly report")
        async def weekly_report(interaction: discord.Interaction) -> None:
            report = self.container.report_service.weekly_progress_report()
            await interaction.response.send_message(report, ephemeral=True)

        self.tree.add_command(dashboard)
        self.tree.add_command(weekly_report)

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:  # pragma: no cover
        logger.exception("Unhandled command error", exc_info=error)
        await ctx.send("An error occurred while executing this command.")


def resolve_actor(user_id: int, role_name: str = "member") -> Actor:
    return Actor(user_id=str(user_id), role=ROLE_MAP.get(role_name.lower(), Role.MEMBER))


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
