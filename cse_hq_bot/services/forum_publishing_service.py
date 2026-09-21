import asyncio
import logging
from collections.abc import Coroutine
from functools import partial
from typing import Any

from cse_hq_bot.errors import ForumPublishingError, InvalidInputError
from cse_hq_bot.forum_models import (
    FORUM_KINDS,
    ForumGateway,
    ForumPublicationResult,
)
from cse_hq_bot.identifiers import bug_code
from cse_hq_bot.models import Actor
from cse_hq_bot.permissions import ensure_can_manage_project
from cse_hq_bot.repositories.forum_repository import ForumRepository

logger = logging.getLogger(__name__)


class ForumPublishingService:
    def __init__(
        self,
        repo: ForumRepository,
        gateway: ForumGateway | None = None,
        *,
        title_limit: int = 100,
        content_limit: int = 1800,
    ):
        self.repo = repo
        self.gateway = gateway
        self.title_limit = max(20, min(100, int(title_limit)))
        self.content_limit = max(200, min(1900, int(content_limit)))
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def set_gateway(self, gateway: ForumGateway) -> None:
        self.gateway = gateway

    async def close(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    async def configure_forums(
        self, actor: Actor, mappings: dict[str, str]
    ) -> list[dict]:
        ensure_can_manage_project(actor)
        if set(mappings) != FORUM_KINDS:
            raise InvalidInputError(
                "Configure Bug, Pull Request, and Release Forum channels together"
            )
        gateway = self._require_gateway()
        normalized: dict[str, str] = {}
        for forum_kind, channel_id in mappings.items():
            clean_id = str(channel_id).strip()
            if not clean_id.isdigit() or int(clean_id) <= 0:
                raise InvalidInputError("Forum channel IDs must be positive numbers")
            await gateway.validate_forum(clean_id)
            normalized[forum_kind] = clean_id
        self.repo.set_forums(normalized, actor.user_id)
        return self.repo.list_forums()

    async def test_publish(self, actor: Actor, forum_kind: str) -> str:
        ensure_can_manage_project(actor)
        forum_kind = self._validate_forum_kind(forum_kind)
        setting = self.repo.get_forum(forum_kind)
        if setting is None:
            raise ForumPublishingError(
                f"The {forum_kind} Forum channel is not configured"
            )
        created = await self._require_gateway().create_post(
            str(setting["forum_channel_id"]),
            self._safe_title(f"CSE-HQ {forum_kind.replace('_', ' ').title()} test"),
            self._safe_content(
                "CSE-HQ Forum publishing test. No project or GitHub record was changed."
            ),
        )
        return created.thread_id

    def publish_bug_nowait(self, event_type: str, bug: dict) -> None:
        if self.repo.get_forum("bug") is None:
            return
        self._schedule(
            self.publish_bug(event_type, bug),
            entity_type="bug",
            entity_id=str(bug.get("id") or "unknown"),
        )

    async def publish_bug(
        self, event_type: str, bug: dict
    ) -> ForumPublicationResult:
        code = bug_code(bug)
        title = f'{code} — {bug.get("title") or "Untitled bug"}'
        content = "\n".join(
            (
                f"**Status:** {bug.get('status') or 'unknown'}",
                f"**Severity:** {bug.get('severity') or 'unknown'}",
                f"**Assignee:** {bug.get('assignee_id') or 'Unassigned'}",
                "",
                str(bug.get("description") or "No description."),
            )
        )
        reply = None
        if event_type != "reported":
            reply = (
                f"Bug updated: status `{bug.get('status') or 'unknown'}`, "
                f"assignee `{bug.get('assignee_id') or 'unassigned'}`."
            )
        return await self.publish_or_update(
            forum_kind="bug",
            entity_type="bug",
            entity_id=str(bug["id"]),
            title=title,
            content=content,
            reply=reply,
        )

    async def publish_or_update(
        self,
        *,
        forum_kind: str,
        entity_type: str,
        entity_id: str,
        title: str,
        content: str,
        reply: str | None = None,
    ) -> ForumPublicationResult:
        forum_kind = self._validate_forum_kind(forum_kind)
        entity_type = self._clean_identifier(entity_type, "entity type")
        entity_id = self._clean_identifier(entity_id, "entity ID")
        setting = self.repo.get_forum(forum_kind)
        if setting is None:
            return ForumPublicationResult("SKIPPED_NOT_CONFIGURED", entity_type, entity_id)
        gateway = self._require_gateway()
        key = (entity_type, entity_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            publication = self.repo.get_publication(entity_type, entity_id)
            safe_title = self._safe_title(title)
            safe_content = self._safe_content(content)
            if publication is not None:
                await gateway.update_post(
                    str(publication["forum_channel_id"]),
                    str(publication["thread_id"]),
                    str(publication["starter_message_id"]),
                    safe_title,
                    safe_content,
                )
                if reply:
                    await gateway.reply(
                        str(publication["thread_id"]), self._safe_content(reply)
                    )
                self.repo.touch_publication(entity_type, entity_id)
                return ForumPublicationResult(
                    "UPDATED",
                    entity_type,
                    entity_id,
                    str(publication["thread_id"]),
                )

            created = await gateway.create_post(
                str(setting["forum_channel_id"]), safe_title, safe_content
            )
            inserted = self.repo.create_publication(
                entity_type=entity_type,
                entity_id=entity_id,
                forum_channel_id=str(setting["forum_channel_id"]),
                thread_id=created.thread_id,
                starter_message_id=created.starter_message_id,
            )
            if not inserted:
                existing = self.repo.get_publication(entity_type, entity_id)
                return ForumPublicationResult(
                    "UPDATED",
                    entity_type,
                    entity_id,
                    str(existing["thread_id"]) if existing else None,
                )
            return ForumPublicationResult(
                "CREATED", entity_type, entity_id, created.thread_id
            )

    async def reply_existing(
        self, *, entity_type: str, entity_id: str, content: str
    ) -> ForumPublicationResult:
        entity_type = self._clean_identifier(entity_type, "entity type")
        entity_id = self._clean_identifier(entity_id, "entity ID")
        publication = self.repo.get_publication(entity_type, entity_id)
        if publication is None:
            return ForumPublicationResult("SKIPPED_NO_PUBLICATION", entity_type, entity_id)
        await self._require_gateway().reply(
            str(publication["thread_id"]), self._safe_content(content)
        )
        self.repo.touch_publication(entity_type, entity_id)
        return ForumPublicationResult(
            "REPLIED", entity_type, entity_id, str(publication["thread_id"])
        )

    def _schedule(
        self,
        operation: Coroutine[Any, Any, Any],
        *,
        entity_type: str,
        entity_id: str,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            logger.warning(
                "Forum publication skipped because no event loop is running",
                extra={
                    "component": "forum_publishing",
                    "operation": "publish",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "result": "skipped",
                    "error_category": "EventLoopUnavailable",
                },
            )
            return
        task = loop.create_task(operation)
        self._background_tasks.add(task)
        task.add_done_callback(
            partial(
                self._publication_finished,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        )

    def _publication_finished(
        self,
        task: asyncio.Task[Any],
        *,
        entity_type: str,
        entity_id: str,
    ) -> None:
        self._background_tasks.discard(task)
        try:
            task.result()
        except Exception as exc:
            logger.exception(
                "Forum publication failed after domain mutation",
                extra={
                    "component": "forum_publishing",
                    "operation": "publish",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "result": "failed",
                    "error_category": exc.__class__.__name__,
                },
            )

    def _require_gateway(self) -> ForumGateway:
        if self.gateway is None:
            raise ForumPublishingError("Discord Forum publishing is not available")
        return self.gateway

    def _validate_forum_kind(self, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in FORUM_KINDS:
            raise InvalidInputError("Unsupported Forum publication type")
        return normalized

    def _clean_identifier(self, value: str, label: str) -> str:
        cleaned = str(value).strip()
        if not cleaned or len(cleaned) > 120:
            raise InvalidInputError(f"Use a valid {label}")
        return cleaned

    def _safe_title(self, value: str) -> str:
        cleaned = " ".join(str(value).split()) or "CSE-HQ update"
        return self._neutralize_mentions(cleaned)[: self.title_limit]

    def _safe_content(self, value: str) -> str:
        cleaned = str(value).strip() or "No details available."
        safe = self._neutralize_mentions(cleaned)
        if len(safe) <= self.content_limit:
            return safe
        return safe[: self.content_limit - 1].rstrip() + "…"

    def _neutralize_mentions(self, value: str) -> str:
        return value.replace("@", "@\u200b")
