import logging
from collections.abc import Callable
from datetime import UTC, datetime

from cse_hq_bot.config import Config
from cse_hq_bot.db import Database
from cse_hq_bot.health_models import HealthComponent, HealthReport, HealthState
from cse_hq_bot.models import Actor
from cse_hq_bot.permissions import ensure_can_manage_project
from cse_hq_bot.repositories.forum_repository import ForumRepository
from cse_hq_bot.services.github_service import GitHubService
from cse_hq_bot.version import application_version as installed_application_version

logger = logging.getLogger(__name__)


class HealthService:
    def __init__(
        self,
        config: Config,
        db: Database,
        github_service: GitHubService,
        forum_repo: ForumRepository,
        *,
        webhook_server: object | None,
        ai_provider: object,
        application_version: str | None = None,
    ):
        self.config = config
        self.db = db
        self.github_service = github_service
        self.forum_repo = forum_repo
        self.webhook_server = webhook_server
        self.ai_provider = ai_provider
        self.application_version = application_version or installed_application_version()

    def get_report(self, actor: Actor, *, discord_ready: bool) -> HealthReport:
        ensure_can_manage_project(actor)
        checks: tuple[tuple[str, str, Callable[[], HealthComponent]], ...] = (
            ("discord", "Discord bot", lambda: self._discord(discord_ready)),
            ("database", "Database", self._database),
            ("groq", "Groq", self._groq),
            ("gemini", "Gemini", self._gemini),
            ("openai_fallback", "OpenAI fallback", self._openai_fallback),
            ("github", "GitHub", self._github),
            ("webhook", "GitHub webhook", self._webhook),
            ("forum_bug", "Bug Forum", lambda: self._forum("bug", "Bug Forum")),
            (
                "forum_pull_request",
                "PR Forum",
                lambda: self._forum("pull_request", "PR Forum"),
            ),
            (
                "forum_release",
                "Release Forum",
                lambda: self._forum("release", "Release Forum"),
            ),
        )
        components = tuple(
            self._safe_check(key, label, check) for key, label, check in checks
        )
        return HealthReport(
            version=self.application_version,
            overall=self._overall(components),
            checked_at=datetime.now(UTC),
            components=components,
        )

    def _safe_check(
        self, key: str, label: str, check: Callable[[], HealthComponent]
    ) -> HealthComponent:
        try:
            return check()
        except Exception as exc:
            logger.exception(
                "Health component check failed",
                extra={
                    "component": key,
                    "operation": "health_check",
                    "result": "failed",
                    "error_category": exc.__class__.__name__,
                },
            )
            return HealthComponent(key, label, HealthState.FAILED, "check unavailable")

    def _discord(self, ready: bool) -> HealthComponent:
        return HealthComponent(
            "discord",
            "Discord bot",
            HealthState.HEALTHY if ready else HealthState.FAILED,
            "connected" if ready else "not ready",
        )

    def _database(self) -> HealthComponent:
        if not self.db.ping():
            raise RuntimeError("Database connectivity check failed")
        return HealthComponent("database", "Database", HealthState.HEALTHY, "connected")

    def _groq(self) -> HealthComponent:
        if self.config.ai_provider != "groq":
            return HealthComponent(
                "groq", "Groq", HealthState.DISABLED, "not selected"
            )
        if not self.config.groq_api_key or not self.config.groq_model:
            return HealthComponent(
                "groq", "Groq", HealthState.FAILED, "configuration incomplete"
            )
        runtime = self._ai_runtime()
        if runtime.get("primary_result") == "failed":
            fallback_available = self._fallback_configured()
            category = self._safe_category(runtime.get("primary_error_category"))
            detail = f"latest request failed ({category})"
            if fallback_available:
                detail += "; fallback configured"
            return HealthComponent(
                "groq",
                "Groq",
                HealthState.DEGRADED if fallback_available else HealthState.FAILED,
                detail,
            )
        return HealthComponent("groq", "Groq", HealthState.HEALTHY, "configured")

    def _gemini(self) -> HealthComponent:
        if self.config.ai_provider != "gemini":
            return HealthComponent(
                "gemini", "Gemini", HealthState.DISABLED, "not selected"
            )
        if not self.config.gemini_api_key or not self.config.ai_model:
            return HealthComponent(
                "gemini", "Gemini", HealthState.FAILED, "configuration incomplete"
            )
        runtime = self._ai_runtime()
        if runtime.get("primary_result") == "failed":
            fallback_available = self._fallback_configured()
            category = self._safe_category(runtime.get("primary_error_category"))
            detail = f"latest request failed ({category})"
            if fallback_available:
                detail += "; fallback configured"
            return HealthComponent(
                "gemini",
                "Gemini",
                HealthState.DEGRADED if fallback_available else HealthState.FAILED,
                detail,
            )
        return HealthComponent("gemini", "Gemini", HealthState.HEALTHY, "configured")

    def _openai_fallback(self) -> HealthComponent:
        if not self.config.ai_fallback_enabled:
            return HealthComponent(
                "openai_fallback",
                "OpenAI fallback",
                HealthState.DISABLED,
                "disabled",
            )
        if not self._fallback_configured():
            return HealthComponent(
                "openai_fallback",
                "OpenAI fallback",
                HealthState.FAILED,
                "configuration incomplete",
            )
        runtime = self._ai_runtime()
        if runtime.get("fallback_result") == "failed":
            category = self._safe_category(runtime.get("fallback_error_category"))
            return HealthComponent(
                "openai_fallback",
                "OpenAI fallback",
                HealthState.DEGRADED,
                f"configured; latest attempt failed ({category})",
            )
        return HealthComponent(
            "openai_fallback",
            "OpenAI fallback",
            HealthState.HEALTHY,
            "configured",
        )

    def _github(self) -> HealthComponent:
        snapshot = self.github_service.health_snapshot()
        if not snapshot.get("enabled"):
            return HealthComponent(
                "github", "GitHub", HealthState.DISABLED, "disabled"
            )
        if not snapshot.get("provider_available"):
            return HealthComponent(
                "github", "GitHub", HealthState.FAILED, "provider unavailable"
            )
        sync = snapshot.get("sync") or {}
        status = str(sync.get("status") or "NEVER_SYNCED").upper()
        timestamp = self._safe_timestamp(sync.get("last_synced_at"))
        if status == "FAILED":
            category = self._safe_category(sync.get("error"))
            return HealthComponent(
                "github",
                "GitHub",
                HealthState.FAILED,
                f"last sync failed ({category}); {timestamp}",
            )
        if status == "SYNCING":
            return HealthComponent(
                "github", "GitHub", HealthState.DEGRADED, "sync in progress"
            )
        if status != "SUCCESS":
            return HealthComponent(
                "github", "GitHub", HealthState.DEGRADED, "configured; never synced"
            )
        if snapshot.get("stale"):
            return HealthComponent(
                "github",
                "GitHub",
                HealthState.DEGRADED,
                f"cache stale; last sync {timestamp}",
            )
        return HealthComponent(
            "github", "GitHub", HealthState.HEALTHY, f"last sync {timestamp}"
        )

    def _webhook(self) -> HealthComponent:
        if not self.config.github_webhook_enabled:
            return HealthComponent(
                "webhook", "GitHub webhook", HealthState.DISABLED, "disabled"
            )
        if self.webhook_server is None or not bool(
            getattr(self.webhook_server, "is_listening", False)
        ):
            return HealthComponent(
                "webhook", "GitHub webhook", HealthState.FAILED, "not listening"
            )
        latest = self.forum_repo.get_latest_delivery()
        if latest is None:
            return HealthComponent(
                "webhook", "GitHub webhook", HealthState.HEALTHY, "listening; no deliveries yet"
            )
        status = str(latest.get("status") or "UNKNOWN").upper()
        timestamp = self._safe_timestamp(
            latest.get("processed_at") or latest.get("received_at")
        )
        if status == "FAILED":
            category = self._safe_category(latest.get("error_code"))
            return HealthComponent(
                "webhook",
                "GitHub webhook",
                HealthState.DEGRADED,
                f"listening; latest delivery failed ({category}); {timestamp}",
            )
        if status == "PROCESSING":
            return HealthComponent(
                "webhook",
                "GitHub webhook",
                HealthState.DEGRADED,
                f"listening; delivery processing; {timestamp}",
            )
        return HealthComponent(
            "webhook",
            "GitHub webhook",
            HealthState.HEALTHY,
            f"listening; latest delivery {status.lower()}; {timestamp}",
        )

    def _forum(self, kind: str, label: str) -> HealthComponent:
        configured = self.forum_repo.get_forum(kind) is not None
        return HealthComponent(
            f"forum_{kind}",
            label,
            HealthState.HEALTHY if configured else HealthState.DISABLED,
            "configured" if configured else "not configured",
        )

    def _ai_runtime(self) -> dict:
        snapshot = getattr(self.ai_provider, "health_snapshot", None)
        if not callable(snapshot):
            return {}
        result = snapshot()
        return result if isinstance(result, dict) else {}

    def _fallback_configured(self) -> bool:
        return bool(
            self.config.ai_fallback_enabled
            and self.config.ai_fallback_provider == "openai"
            and self.config.openai_api_key
            and self.config.openai_model
        )

    def _overall(self, components: tuple[HealthComponent, ...]) -> HealthState:
        states = {component.key: component.state for component in components}
        if any(states.get(key) == HealthState.FAILED for key in ("discord", "database")):
            return HealthState.FAILED
        if any(
            component.state in {HealthState.DEGRADED, HealthState.FAILED}
            for component in components
        ):
            return HealthState.DEGRADED
        return HealthState.HEALTHY

    def _safe_timestamp(self, value: object) -> str:
        if not value:
            return "time unavailable"
        text = " ".join(str(value).split())
        return text[:32] or "time unavailable"

    def _safe_category(self, value: object) -> str:
        text = "".join(character for character in str(value or "unknown") if character.isalnum() or character in "_-")
        return text[:64] or "unknown"
