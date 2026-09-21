from dataclasses import dataclass
from typing import Protocol

FORUM_KINDS = frozenset({"bug", "pull_request", "release"})


@dataclass(frozen=True)
class CreatedForumPost:
    thread_id: str
    starter_message_id: str


@dataclass(frozen=True)
class ForumPublicationResult:
    outcome: str
    entity_type: str
    entity_id: str
    thread_id: str | None = None


@dataclass(frozen=True)
class GitHubWebhookOutcome:
    status: str
    http_status: int
    detail: str


class ForumGateway(Protocol):
    async def validate_forum(self, forum_channel_id: str) -> None: ...

    async def create_post(
        self, forum_channel_id: str, title: str, content: str
    ) -> CreatedForumPost: ...

    async def update_post(
        self,
        forum_channel_id: str,
        thread_id: str,
        starter_message_id: str,
        title: str,
        content: str,
    ) -> None: ...

    async def reply(self, thread_id: str, content: str) -> None: ...
