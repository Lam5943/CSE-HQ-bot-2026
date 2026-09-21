from typing import Any

import discord

from cse_hq_bot.errors import ForumPublishingError, InvalidInputError
from cse_hq_bot.forum_models import CreatedForumPost


class DiscordForumGateway:
    def __init__(self, client: discord.Client):
        self.client = client

    async def validate_forum(self, forum_channel_id: str) -> None:
        channel = await self._get_channel(forum_channel_id)
        if not isinstance(channel, discord.ForumChannel):
            raise InvalidInputError("Select an existing Discord Forum channel")
        bot_member = channel.guild.me
        if bot_member is None:
            raise InvalidInputError("The bot is not available in that Forum's server")
        permissions = channel.permissions_for(bot_member)
        required = (
            "view_channel",
            "send_messages",
            "send_messages_in_threads",
            "create_public_threads",
        )
        missing = [name for name in required if not getattr(permissions, name, False)]
        if missing:
            raise InvalidInputError(
                "Missing Forum permissions: " + ", ".join(sorted(missing))
            )

    async def create_post(
        self, forum_channel_id: str, title: str, content: str
    ) -> CreatedForumPost:
        channel = await self._get_channel(forum_channel_id)
        if not isinstance(channel, discord.ForumChannel):
            raise ForumPublishingError("Configured channel is no longer a Forum")
        created = await channel.create_thread(
            name=title,
            content=content,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        thread = getattr(created, "thread", None)
        message = getattr(created, "message", None)
        if thread is None:
            raise ForumPublishingError("Discord did not return the created Forum post")
        starter_id = getattr(message, "id", None) or getattr(thread, "id", None)
        return CreatedForumPost(str(thread.id), str(starter_id))

    async def update_post(
        self,
        forum_channel_id: str,
        thread_id: str,
        starter_message_id: str,
        title: str,
        content: str,
    ) -> None:
        thread = await self._get_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            raise ForumPublishingError("The mapped Forum post no longer exists")
        if thread.name != title:
            await thread.edit(name=title)
        message = await thread.fetch_message(int(starter_message_id))
        await message.edit(
            content=content, allowed_mentions=discord.AllowedMentions.none()
        )

    async def reply(self, thread_id: str, content: str) -> None:
        thread = await self._get_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            raise ForumPublishingError("The mapped Forum post no longer exists")
        await thread.send(content, allowed_mentions=discord.AllowedMentions.none())

    async def _get_channel(self, channel_id: str) -> Any:
        numeric_id = int(channel_id)
        channel = self.client.get_channel(numeric_id)
        if channel is not None:
            return channel
        try:
            return await self.client.fetch_channel(numeric_id)
        except discord.DiscordException as exc:
            raise ForumPublishingError("Discord Forum channel is unavailable") from exc
