import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from cse_hq_bot.bot import CSEHQBot
from cse_hq_bot.db import Database
from cse_hq_bot.errors import PermissionDeniedError
from cse_hq_bot.health_models import HealthState
from cse_hq_bot.logging_config import OperationalFormatter
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.repositories.forum_repository import ForumRepository
from cse_hq_bot.services.health_service import HealthService
from cse_hq_bot.ui import build_health_embed


class StubGitHubService:
    def __init__(self, snapshot: dict | None = None, error: Exception | None = None):
        self.snapshot = snapshot or {"enabled": False}
        self.error = error

    def health_snapshot(self) -> dict:
        if self.error is not None:
            raise self.error
        return self.snapshot


class StubAIProvider:
    def __init__(self, snapshot: dict | None = None):
        self.snapshot = snapshot or {}

    def health_snapshot(self) -> dict:
        return self.snapshot


class StubWebhookServer:
    def __init__(self, listening: bool):
        self.is_listening = listening


class FailingDatabase:
    def ping(self) -> bool:
        raise OSError("database unavailable")


def make_config(**overrides):
    values = {
        "ai_provider": "gemini",
        "gemini_api_key": "gemini-super-secret",
        "ai_model": "private-model-name",
        "groq_api_key": "groq-super-secret",
        "groq_model": "private-groq-model",
        "ai_fallback_enabled": True,
        "ai_fallback_provider": "openai",
        "openai_api_key": "openai-super-secret",
        "openai_model": "private-fallback-model",
        "github_token": "github-super-secret",
        "github_webhook_enabled": True,
        "github_webhook_secret": "webhook-super-secret",
        "database_path": "private-database-path.db",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_health_service(
    tmp_path: Path,
    *,
    config=None,
    github_snapshot: dict | None = None,
    github_error: Exception | None = None,
    ai_snapshot: dict | None = None,
    webhook_listening: bool = True,
    configure_forums: bool = True,
    database=None,
) -> tuple[HealthService, ForumRepository]:
    db = Database(str(tmp_path / "health.db"))
    db.initialize()
    forum_repo = ForumRepository(db)
    if configure_forums:
        forum_repo.set_forums(
            {
                "bug": "111111",
                "pull_request": "222222",
                "release": "333333",
            },
            "leader",
        )
    service = HealthService(
        config or make_config(),
        database or db,
        StubGitHubService(
            github_snapshot
            or {
                "enabled": True,
                "provider_available": True,
                "sync": {
                    "status": "SUCCESS",
                    "last_synced_at": "2026-09-21 10:00:00",
                    "error": None,
                },
                "stale": False,
            },
            github_error,
        ),
        forum_repo,
        webhook_server=StubWebhookServer(webhook_listening),
        ai_provider=StubAIProvider(ai_snapshot),
        application_version="1.2.3",
    )
    return service, forum_repo


def component(report, key: str):
    return next(item for item in report.components if item.key == key)


def test_health_all_components_healthy(tmp_path: Path):
    service, repo = make_health_service(tmp_path)
    repo.claim_delivery("delivery-1", "pull_request")
    repo.finish_delivery("delivery-1", "PROCESSED")

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert report.overall == HealthState.HEALTHY
    assert component(report, "groq").state == HealthState.DISABLED
    assert all(
        item.state == HealthState.HEALTHY
        for item in report.components
        if item.key != "groq"
    )
    assert report.version == "1.2.3"
    assert "last sync" in component(report, "github").detail
    assert "latest delivery processed" in component(report, "webhook").detail


def test_optional_components_disabled_do_not_degrade_health(tmp_path: Path):
    service, _ = make_health_service(
        tmp_path,
        config=make_config(
            ai_provider="fake",
            ai_fallback_enabled=False,
            github_webhook_enabled=False,
        ),
        github_snapshot={"enabled": False},
        configure_forums=False,
    )

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert report.overall == HealthState.HEALTHY
    assert component(report, "groq").state == HealthState.DISABLED
    assert component(report, "gemini").state == HealthState.DISABLED
    assert component(report, "openai_fallback").state == HealthState.DISABLED
    assert component(report, "github").state == HealthState.DISABLED
    assert component(report, "webhook").state == HealthState.DISABLED


def test_groq_primary_health_uses_runtime_state(tmp_path: Path):
    service, _ = make_health_service(
        tmp_path,
        config=make_config(
            ai_provider="groq",
            ai_fallback_enabled=False,
        ),
        ai_snapshot={
            "primary_result": "success",
            "primary_error_category": None,
        },
    )

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert component(report, "groq").state == HealthState.HEALTHY
    assert component(report, "gemini").state == HealthState.DISABLED
    assert report.overall == HealthState.HEALTHY


def test_failed_ai_primary_with_configured_fallback_is_degraded(tmp_path: Path):
    service, _ = make_health_service(
        tmp_path,
        ai_snapshot={
            "primary_result": "failed",
            "primary_error_category": "AITimeoutError",
            "fallback_result": "success",
        },
    )

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert report.overall == HealthState.DEGRADED
    assert component(report, "gemini").state == HealthState.DEGRADED
    assert component(report, "openai_fallback").state == HealthState.HEALTHY


def test_database_failure_isolated_and_marks_overall_failed(tmp_path: Path):
    service, _ = make_health_service(tmp_path, database=FailingDatabase())

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert report.overall == HealthState.FAILED
    assert component(report, "database").state == HealthState.FAILED
    assert component(report, "groq").state == HealthState.DISABLED
    assert component(report, "gemini").state == HealthState.HEALTHY
    assert len(report.components) == 10


def test_github_failure_isolated_and_reported(tmp_path: Path):
    service, _ = make_health_service(
        tmp_path, github_error=OSError("network detail must not leak")
    )

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert report.overall == HealthState.DEGRADED
    assert component(report, "github").state == HealthState.FAILED
    assert component(report, "github").detail == "check unavailable"


def test_webhook_disabled_and_listener_failure_states(tmp_path: Path):
    disabled, _ = make_health_service(
        tmp_path / "disabled",
        config=make_config(github_webhook_enabled=False),
    )
    stopped, _ = make_health_service(
        tmp_path / "stopped", webhook_listening=False
    )

    disabled_report = disabled.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )
    stopped_report = stopped.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert component(disabled_report, "webhook").state == HealthState.DISABLED
    assert component(stopped_report, "webhook").state == HealthState.FAILED
    assert stopped_report.overall == HealthState.DEGRADED


def test_latest_failed_webhook_delivery_is_degraded(tmp_path: Path):
    service, repo = make_health_service(tmp_path)
    repo.claim_delivery("delivery-failed", "workflow_run")
    repo.finish_delivery("delivery-failed", "FAILED", "ForumPublishingError")

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    webhook = component(report, "webhook")
    assert webhook.state == HealthState.DEGRADED
    assert "ForumPublishingError" in webhook.detail


def test_missing_forum_configuration_is_visible_but_optional(tmp_path: Path):
    service, _ = make_health_service(tmp_path, configure_forums=False)

    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )

    assert report.overall == HealthState.HEALTHY
    assert all(
        component(report, key).state == HealthState.DISABLED
        for key in ("forum_bug", "forum_pull_request", "forum_release")
    )


def test_health_report_and_embed_do_not_leak_configuration(tmp_path: Path):
    service, _ = make_health_service(tmp_path)
    report = service.get_report(
        Actor("leader", Role.LEADER), discord_ready=True
    )
    rendered = repr(asdict(report)) + repr(build_health_embed(report).to_dict())

    for secret in (
        "gemini-super-secret",
        "groq-super-secret",
        "openai-super-secret",
        "github-super-secret",
        "webhook-super-secret",
        "private-model-name",
        "private-groq-model",
        "private-fallback-model",
        "private-database-path.db",
        "111111",
        "222222",
        "333333",
    ):
        assert secret not in rendered


def test_health_requires_leadership_and_command_is_restricted(tmp_path: Path):
    service, _ = make_health_service(tmp_path)
    with pytest.raises(PermissionDeniedError):
        service.get_report(Actor("member", Role.MEMBER), discord_ready=True)

    bot = CSEHQBot(SimpleNamespace(health_service=service))

    class Response:
        def __init__(self):
            self.message = None

        async def send_message(self, content=None, **kwargs):
            self.message = content
            self.kwargs = kwargs

    async def runner():
        await bot.setup_hook()
        command = bot.tree.get_command("health")
        assert command is not None
        response = Response()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42), response=response
        )
        await command.callback(interaction)
        assert "leaders and co-leads" in response.message
        assert response.kwargs["ephemeral"] is True
        await bot.close()

    asyncio.run(runner())


def test_operational_formatter_emits_allowlisted_fields_only():
    record = logging.LogRecord(
        "cse_hq_bot.test",
        logging.ERROR,
        __file__,
        1,
        "operation failed",
        (),
        None,
    )
    record.component = "github_webhook"
    record.operation = "process_delivery"
    record.delivery_id = "delivery-123"
    record.result = "failed"
    record.error_category = "ForumPublishingError"
    record.api_key = "must-not-appear"

    rendered = OperationalFormatter("%(message)s").format(record)

    assert "component=github_webhook" in rendered
    assert "operation=process_delivery" in rendered
    assert "delivery_id=delivery-123" in rendered
    assert "error_category=ForumPublishingError" in rendered
    assert "must-not-appear" not in rendered
