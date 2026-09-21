import hashlib
import hmac
import json
import logging
from collections.abc import Mapping

from aiohttp import web

from cse_hq_bot.errors import (
    GitHubWebhookConfigurationError,
    GitHubWebhookSignatureError,
)
from cse_hq_bot.forum_models import GitHubWebhookOutcome
from cse_hq_bot.repositories.forum_repository import ForumRepository
from cse_hq_bot.services.github_event_service import GitHubEventService

logger = logging.getLogger(__name__)


class GitHubWebhookProcessor:
    def __init__(
        self,
        repo: ForumRepository,
        event_service: GitHubEventService,
        *,
        secret: str,
        repository_full_name: str,
    ):
        if not secret:
            raise GitHubWebhookConfigurationError(
                "GITHUB_WEBHOOK_SECRET is required when webhooks are enabled"
            )
        owner, separator, name = repository_full_name.partition("/")
        if (
            not separator
            or not owner.strip()
            or not name.strip()
            or "/" in name
        ):
            raise GitHubWebhookConfigurationError(
                "A single GitHub repository must be configured for webhooks"
            )
        self.repo = repo
        self.event_service = event_service
        self.secret = secret.encode("utf-8")
        self.repository_full_name = repository_full_name.casefold()

    async def process(
        self, headers: Mapping[str, str], body: bytes
    ) -> GitHubWebhookOutcome:
        normalized_headers = {str(key).lower(): value for key, value in headers.items()}
        self._verify_signature(
            str(normalized_headers.get("x-hub-signature-256") or ""), body
        )
        delivery_id = str(normalized_headers.get("x-github-delivery") or "").strip()
        event_type = str(normalized_headers.get("x-github-event") or "").strip().lower()
        if not delivery_id or not event_type:
            return GitHubWebhookOutcome("REJECTED", 400, "Missing GitHub webhook headers")
        if len(delivery_id) > 128 or len(event_type) > 64:
            return GitHubWebhookOutcome("REJECTED", 400, "Invalid GitHub webhook headers")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return GitHubWebhookOutcome("REJECTED", 400, "Invalid JSON payload")
        if not isinstance(payload, dict):
            return GitHubWebhookOutcome("REJECTED", 400, "Invalid JSON payload")
        repository = payload.get("repository")
        full_name = (
            str(repository.get("full_name") or "").casefold()
            if isinstance(repository, dict)
            else ""
        )
        if full_name != self.repository_full_name:
            logger.info(
                "GitHub webhook ignored",
                extra={
                    "component": "github_webhook",
                    "operation": "process_delivery",
                    "delivery_id": delivery_id,
                    "result": "repository_mismatch",
                },
            )
            return GitHubWebhookOutcome("IGNORED", 202, "Repository does not match")
        if not self.repo.claim_delivery(delivery_id, event_type):
            logger.info(
                "GitHub webhook duplicate ignored",
                extra={
                    "component": "github_webhook",
                    "operation": "process_delivery",
                    "delivery_id": delivery_id,
                    "result": "duplicate",
                },
            )
            return GitHubWebhookOutcome("DUPLICATE", 202, "Delivery already processed")
        try:
            result = await self.event_service.handle(event_type, payload)
        except Exception as exc:
            self.repo.finish_delivery(delivery_id, "FAILED", exc.__class__.__name__)
            logger.exception(
                "GitHub webhook processing failed",
                extra={
                    "component": "github_webhook",
                    "operation": "process_delivery",
                    "delivery_id": delivery_id,
                    "event_type": event_type,
                    "result": "failed",
                    "error_category": exc.__class__.__name__,
                },
            )
            return GitHubWebhookOutcome(
                "FAILED", 503, "Webhook event could not be published"
            )
        self.repo.finish_delivery(delivery_id, "PROCESSED")
        logger.info(
            "GitHub webhook processed",
            extra={
                "component": "github_webhook",
                "operation": "process_delivery",
                "delivery_id": delivery_id,
                "event_type": event_type,
                "result": "success",
            },
        )
        return GitHubWebhookOutcome("PROCESSED", 202, result)

    def _verify_signature(self, signature: str, body: bytes) -> None:
        expected = "sha256=" + hmac.new(
            self.secret, body, hashlib.sha256
        ).hexdigest()
        if not signature.startswith("sha256=") or not hmac.compare_digest(
            signature, expected
        ):
            raise GitHubWebhookSignatureError("Invalid GitHub webhook signature")


class GitHubWebhookServer:
    def __init__(
        self,
        processor: GitHubWebhookProcessor,
        *,
        host: str,
        port: int,
        path: str,
    ):
        if not path.startswith("/") or "{" in path or "}" in path:
            raise GitHubWebhookConfigurationError(
                "GITHUB_WEBHOOK_PATH must be a fixed absolute path"
            )
        self.processor = processor
        self.host = host
        self.port = port
        self.path = path
        self._runner: web.AppRunner | None = None

    @property
    def is_listening(self) -> bool:
        return self._runner is not None

    async def start(self) -> None:
        app = web.Application(client_max_size=1_000_000)
        app.router.add_post(self.path, self._handle_request)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        try:
            await site.start()
        except Exception:
            await self._runner.cleanup()
            self._runner = None
            raise

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _handle_request(self, request: web.Request) -> web.Response:
        body = await request.read()
        try:
            result = await self.processor.process(request.headers, body)
        except GitHubWebhookSignatureError as exc:
            logger.warning(
                "GitHub webhook rejected",
                extra={
                    "component": "github_webhook",
                    "operation": "verify_signature",
                    "result": "rejected",
                    "error_category": exc.__class__.__name__,
                },
            )
            return web.json_response(
                {"status": "REJECTED", "detail": "Invalid signature"}, status=401
            )
        return web.json_response(
            {"status": result.status, "detail": result.detail},
            status=result.http_status,
        )
