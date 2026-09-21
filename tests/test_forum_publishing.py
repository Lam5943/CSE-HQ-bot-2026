import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

import cse_hq_bot.discord_forum_gateway as forum_gateway_module
from cse_hq_bot.bot import CSEHQBot
from cse_hq_bot.config import load_config
from cse_hq_bot.db import Database
from cse_hq_bot.discord_forum_gateway import DiscordForumGateway
from cse_hq_bot.errors import (
    ForumPublishingError,
    GitHubWebhookConfigurationError,
    GitHubWebhookSignatureError,
    InvalidInputError,
    InvalidTransitionError,
    PermissionDeniedError,
)
from cse_hq_bot.factory import ServiceContainer
from cse_hq_bot.forum_models import CreatedForumPost
from cse_hq_bot.github_webhook import GitHubWebhookProcessor
from cse_hq_bot.models import Actor, BugStatus, Role
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.forum_repository import ForumRepository
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.forum_publishing_service import ForumPublishingService
from cse_hq_bot.services.github_event_service import GitHubEventService


class FakeForumGateway:
    def __init__(self, valid_channels=None):
        self.valid_channels = set(valid_channels or {"101", "102", "103"})
        self.created = []
        self.updated = []
        self.replies = []
        self.fail_create = False

    async def validate_forum(self, forum_channel_id: str) -> None:
        if forum_channel_id not in self.valid_channels:
            raise InvalidInputError("Missing Forum permissions")

    async def create_post(self, forum_channel_id: str, title: str, content: str):
        if self.fail_create:
            raise ForumPublishingError("Discord unavailable")
        thread_id = str(1000 + len(self.created))
        result = CreatedForumPost(thread_id, str(2000 + len(self.created)))
        self.created.append((forum_channel_id, title, content, result))
        return result

    async def update_post(
        self,
        forum_channel_id: str,
        thread_id: str,
        starter_message_id: str,
        title: str,
        content: str,
    ) -> None:
        self.updated.append(
            (
                forum_channel_id,
                thread_id,
                starter_message_id,
                title,
                content,
            )
        )

    async def reply(self, thread_id: str, content: str) -> None:
        self.replies.append((thread_id, content))


@pytest.fixture
def forum_stack(tmp_path):
    db = Database(str(tmp_path / "forums.db"))
    db.initialize()
    repo = ForumRepository(db)
    gateway = FakeForumGateway()
    service = ForumPublishingService(repo, gateway)
    leader = Actor("leader", Role.LEADER)
    asyncio.run(
        service.configure_forums(
            leader,
            {"bug": "101", "pull_request": "102", "release": "103"},
        )
    )
    return {
        "db": db,
        "repo": repo,
        "gateway": gateway,
        "service": service,
        "leader": leader,
    }


def _payload(repository="Lam5943/CSE-HQ-bot-2026", **values):
    return {"repository": {"full_name": repository}, **values}


def _headers(body: bytes, delivery: str, event: str, secret="webhook-secret"):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": event,
    }


def _processor(stack, *, bug_label="bug"):
    events = GitHubEventService(stack["service"], bug_label=bug_label)
    return GitHubWebhookProcessor(
        stack["repo"],
        events,
        secret="webhook-secret",
        repository_full_name="Lam5943/CSE-HQ-bot-2026",
    )


def _process(processor, payload, delivery, event, *, secret="webhook-secret"):
    body = json.dumps(payload).encode()
    return asyncio.run(
        processor.process(_headers(body, delivery, event, secret), body)
    )


def test_forum_setup_persists_validated_channels_and_test_publish(tmp_path):
    db = Database(str(tmp_path / "setup.db"))
    db.initialize()
    repo = ForumRepository(db)
    gateway = FakeForumGateway()
    service = ForumPublishingService(repo, gateway)

    skipped = asyncio.run(
        service.publish_or_update(
            forum_kind="bug",
            entity_type="bug",
            entity_id="1",
            title="Not configured",
            content="Safe no-op",
        )
    )
    assert skipped.outcome == "SKIPPED_NOT_CONFIGURED"
    assert gateway.created == []

    with pytest.raises(PermissionDeniedError):
        asyncio.run(
            service.configure_forums(
                Actor("member", Role.MEMBER),
                {"bug": "101", "pull_request": "102", "release": "103"},
            )
        )
    assert repo.list_forums() == []

    with pytest.raises(InvalidInputError, match="permissions"):
        asyncio.run(
            service.configure_forums(
                Actor("leader", Role.LEADER),
                {"bug": "999", "pull_request": "102", "release": "103"},
            )
        )
    assert repo.list_forums() == []

    saved = asyncio.run(
        service.configure_forums(
            Actor("leader", Role.LEADER),
            {"bug": "101", "pull_request": "102", "release": "103"},
        )
    )
    assert {item["forum_kind"] for item in saved} == {
        "bug",
        "pull_request",
        "release",
    }
    thread_id = asyncio.run(
        service.test_publish(Actor("co", Role.CO_LEAD), "bug")
    )
    assert thread_id == "1000"
    assert "No project or GitHub record was changed" in gateway.created[0][2]


def test_discord_gateway_rejects_missing_required_forum_permissions(monkeypatch):
    class FakeForumChannel:
        guild = SimpleNamespace(me=object())

        def permissions_for(self, _member):
            return SimpleNamespace(
                view_channel=True,
                send_messages=True,
                send_messages_in_threads=False,
                create_public_threads=True,
            )

    channel = FakeForumChannel()
    client = SimpleNamespace(get_channel=lambda _channel_id: channel)
    monkeypatch.setattr(
        forum_gateway_module.discord, "ForumChannel", FakeForumChannel
    )

    with pytest.raises(InvalidInputError, match="send_messages_in_threads"):
        asyncio.run(DiscordForumGateway(client).validate_forum("101"))


def test_first_publish_creates_mapping_and_concurrent_update_reuses_thread(
    forum_stack,
):
    service = forum_stack["service"]
    bug = {
        "id": 14,
        "title": "Dashboard crashes",
        "description": "Steps to reproduce",
        "status": "open",
        "severity": 4,
        "assignee_id": None,
    }

    async def scenario():
        return await asyncio.gather(
            service.publish_bug("reported", bug),
            service.publish_bug("status_changed", {**bug, "status": "triaged"}),
        )

    results = asyncio.run(scenario())
    mapping = forum_stack["repo"].get_publication("bug", "14")
    assert mapping["thread_id"] == "1000"
    assert len(forum_stack["gateway"].created) == 1
    assert len(forum_stack["gateway"].updated) == 1
    assert {result.outcome for result in results} == {"CREATED", "UPDATED"}


def test_bug_domain_events_publish_and_forum_failure_does_not_rollback(
    forum_stack,
):
    service = forum_stack["service"]
    bug_service = BugService(
        BugRepository(forum_stack["db"]),
        publication_hook=service.publish_bug_nowait,
    )
    actor = forum_stack["leader"]

    async def successful_flow():
        bug_id = bug_service.report_bug(actor, "Domain bug", "Details", 3)
        await asyncio.sleep(0)
        bug_service.assign_bug(actor, bug_id, "alice")
        await asyncio.sleep(0)
        return bug_id

    bug_id = asyncio.run(successful_flow())
    assert forum_stack["repo"].get_publication("bug", str(bug_id)) is not None
    assert len(forum_stack["gateway"].created) == 1
    assert len(forum_stack["gateway"].updated) == 1

    forum_stack["gateway"].fail_create = True

    async def failed_publication():
        created_id = bug_service.report_bug(actor, "Still committed", "", 2)
        await asyncio.sleep(0)
        return created_id

    failed_id = asyncio.run(failed_publication())
    assert bug_service.get_bug(actor, failed_id)["title"] == "Still committed"
    assert forum_stack["repo"].get_publication("bug", str(failed_id)) is None


def test_webhook_signature_repository_and_delivery_deduplication(forum_stack):
    processor = _processor(forum_stack)
    payload = _payload(action="opened", pull_request={"number": 14})
    body = json.dumps(payload).encode()
    with pytest.raises(GitHubWebhookSignatureError):
        asyncio.run(
            processor.process(_headers(body, "bad", "pull_request", "wrong"), body)
        )

    wrong_repo = _process(
        processor,
        _payload("someone/else", action="opened", pull_request={"number": 14}),
        "wrong-repo",
        "pull_request",
    )
    assert wrong_repo.status == "IGNORED"
    assert forum_stack["repo"].get_delivery("wrong-repo") is None

    valid_payload = _payload(
        action="opened",
        number=14,
        pull_request={
            "number": 14,
            "title": "Forum events",
            "body": "Ready",
            "state": "open",
            "html_url": "https://github.test/pr/14",
            "user": {"login": "dev"},
        },
    )
    first = _process(processor, valid_payload, "delivery-1", "pull_request")
    duplicate = _process(processor, valid_payload, "delivery-1", "pull_request")
    assert first.status == "PROCESSED"
    assert duplicate.status == "DUPLICATE"
    assert len(forum_stack["gateway"].created) == 1


def test_malformed_unsupported_and_failed_webhooks_are_safe(forum_stack):
    processor = _processor(forum_stack)
    malformed = b"not-json"
    malformed_result = asyncio.run(
        processor.process(
            _headers(malformed, "malformed", "pull_request"), malformed
        )
    )
    assert malformed_result.http_status == 400
    assert forum_stack["repo"].get_delivery("malformed") is None

    unsupported_payload = _payload(action="created")
    unsupported = _process(
        processor, unsupported_payload, "unsupported", "discussion"
    )
    assert unsupported.status == "PROCESSED"
    assert unsupported.detail == "IGNORED_UNSUPPORTED_EVENT"

    forum_stack["gateway"].fail_create = True
    failed_payload = _payload(
        action="opened",
        number=25,
        pull_request={
            "number": 25,
            "title": "Cannot publish",
            "state": "open",
        },
    )
    failed = _process(processor, failed_payload, "failed-publish", "pull_request")
    assert failed.status == "FAILED"
    assert failed.http_status == 503
    assert forum_stack["repo"].get_delivery("failed-publish")["status"] == "FAILED"
    assert forum_stack["repo"].get_publication("pull_request", "25") is None


def test_pull_request_updates_and_ci_reuse_one_forum_post(forum_stack):
    processor = _processor(forum_stack)
    opened = _payload(
        action="opened",
        number=14,
        pull_request={
            "number": 14,
            "title": "GitHub event notifications",
            "body": "Adds notifications",
            "state": "open",
            "merged": False,
            "html_url": "https://github.test/pr/14",
            "user": {"login": "dev"},
        },
    )
    merged = {
        **opened,
        "action": "closed",
        "pull_request": {**opened["pull_request"], "merged": True, "state": "closed"},
    }
    closed = {
        **opened,
        "action": "closed",
        "pull_request": {**opened["pull_request"], "merged": False, "state": "closed"},
    }
    reopened = {**opened, "action": "reopened"}
    ready = {**opened, "action": "ready_for_review"}
    ci = _payload(
        action="completed",
        workflow_run={
            "name": "tests",
            "conclusion": "failure",
            "html_url": "https://github.test/runs/1",
            "pull_requests": [{"number": 14}],
        },
    )

    assert _process(processor, opened, "pr-open", "pull_request").status == "PROCESSED"
    assert _process(processor, closed, "pr-closed", "pull_request").status == "PROCESSED"
    assert _process(processor, reopened, "pr-reopened", "pull_request").status == "PROCESSED"
    assert _process(processor, ready, "pr-ready", "pull_request").status == "PROCESSED"
    assert _process(processor, merged, "pr-merged", "pull_request").status == "PROCESSED"
    assert _process(processor, ci, "ci-1", "workflow_run").status == "PROCESSED"

    mapping = forum_stack["repo"].get_publication("pull_request", "14")
    assert mapping["thread_id"] == "1000"
    assert len(forum_stack["gateway"].created) == 1
    assert len(forum_stack["gateway"].updated) == 4
    assert any("failure" in reply[1] for reply in forum_stack["gateway"].replies)
    assert any("closed" in reply[1] for reply in forum_stack["gateway"].replies)
    assert any("ready for review" in reply[1] for reply in forum_stack["gateway"].replies)
    assert any("merged" in reply[1] for reply in forum_stack["gateway"].replies)


def test_release_and_explicit_github_bug_label_routing(forum_stack):
    processor = _processor(forum_stack, bug_label="defect")
    unlabeled = _payload(
        action="opened",
        issue={
            "number": 22,
            "title": "Not a bug",
            "labels": [{"name": "question"}],
        },
    )
    assert _process(processor, unlabeled, "issue-ignore", "issues").detail == "IGNORED_NOT_BUG"

    bug = _payload(
        action="opened",
        issue={
            "number": 22,
            "title": "Crash",
            "body": "Repro",
            "state": "open",
            "labels": [{"name": "Defect"}],
            "html_url": "https://github.test/issues/22",
            "user": {"login": "reporter"},
        },
    )
    release = _payload(
        action="published",
        release={
            "id": 9,
            "tag_name": "v1.2.0",
            "name": "Project Memory Update",
            "body": "Release notes",
            "html_url": "https://github.test/releases/9",
        },
    )
    _process(processor, bug, "issue-bug", "issues")
    _process(processor, release, "release-9", "release")

    assert forum_stack["repo"].get_publication("github_issue", "22") is not None
    assert forum_stack["repo"].get_publication("release", "9") is not None
    titles = [item[1] for item in forum_stack["gateway"].created]
    assert any(title.startswith("GH-ISSUE-22") for title in titles)
    assert any(title.startswith("v1.2.0") for title in titles)


def test_untrusted_mentions_and_long_content_are_bounded(forum_stack):
    result = asyncio.run(
        forum_stack["service"].publish_or_update(
            forum_kind="release",
            entity_type="release",
            entity_id="unsafe",
            title="@everyone " + "x" * 200,
            content="@here <@123> " + "y" * 5000,
        )
    )
    assert result.outcome == "CREATED"
    _, title, content, _ = forum_stack["gateway"].created[0]
    assert "@everyone" not in title
    assert "@here" not in content
    assert "<@123>" not in content
    assert len(title) <= 100
    assert len(content) <= 1800


def test_webhook_configuration_loads_without_forum_channel_env(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBHOOK_PORT", "9000")
    monkeypatch.setenv("GITHUB_WEBHOOK_PATH", "/hooks/github")
    monkeypatch.setenv("GITHUB_BUG_LABEL", "defect")

    config = load_config()

    assert config.github_webhook_enabled is True
    assert config.github_webhook_secret == "secret"
    assert config.webhook_host == "127.0.0.1"
    assert config.webhook_port == 9000
    assert config.github_webhook_path == "/hooks/github"
    assert config.github_bug_label == "defect"
    assert not any("FORUM_CHANNEL" in key for key in vars(config))


def test_enabled_webhook_factory_requires_secret_and_single_repository(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "webhook-config.db"))
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("GITHUB_ENABLED", "false")
    monkeypatch.setenv("GITHUB_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "Lam5943")
    monkeypatch.setenv("GITHUB_REPOSITORY_NAME", "CSE-HQ-bot-2026")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    with pytest.raises(GitHubWebhookConfigurationError, match="SECRET"):
        ServiceContainer(load_config())

    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "configured-secret")
    container = ServiceContainer(load_config())
    assert container.github_webhook_processor is not None
    assert container.github_webhook_server is not None


def test_bug_service_does_not_publish_unchanged_updates(tmp_path):
    db = Database(str(tmp_path / "unchanged.db"))
    db.initialize()
    events = []
    service = BugService(
        BugRepository(db), publication_hook=lambda event, bug: events.append((event, bug))
    )
    actor = Actor("leader", Role.LEADER)
    bug_id = service.report_bug(actor, "Stable", "", 2)
    service.transition_status(actor, bug_id, BugStatus.TRIAGED.value)
    with pytest.raises(InvalidTransitionError):
        service.transition_status(actor, bug_id, BugStatus.TRIAGED.value)
    assert [event for event, _ in events] == ["reported", "status_changed"]


def test_setup_forums_command_is_registered():
    bot = CSEHQBot(SimpleNamespace())

    async def runner():
        await bot.setup_hook()
        setup = bot.tree.get_command("setup")
        assert setup is not None
        assert setup.get_command("forums") is not None
        await bot.close()

    asyncio.run(runner())
