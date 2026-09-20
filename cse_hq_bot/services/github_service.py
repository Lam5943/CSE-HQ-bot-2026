import asyncio
from dataclasses import asdict
from datetime import UTC, datetime

from cse_hq_bot.errors import GitHubConfigurationError, InvalidInputError, NotFoundError
from cse_hq_bot.github_provider import GitHubProvider
from cse_hq_bot.models import Actor
from cse_hq_bot.permissions import ensure_can_manage_project
from cse_hq_bot.repositories.github_repository import GitHubRepositoryCache
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.task_service import TaskService


class GitHubService:
    def __init__(
        self,
        repo: GitHubRepositoryCache,
        provider: GitHubProvider | None,
        task_service: TaskService,
        bug_service: BugService,
        *,
        enabled: bool,
        max_results: int,
        cache_ttl: int = 300,
    ):
        self.repo = repo
        self.provider = provider
        self.task_service = task_service
        self.bug_service = bug_service
        self.enabled = enabled
        self.max_results = max(1, min(int(max_results), 100))
        self.cache_ttl = max(1, int(cache_ttl))

    async def sync_all(self, actor: Actor) -> dict:
        ensure_can_manage_project(actor)
        provider = self._require_provider()
        self.repo.set_sync_state("SYNCING")
        try:
            repository = await provider.get_repository()
            issues, pull_requests, commits, branches = await asyncio.gather(
                provider.list_issues(state="all"),
                provider.list_pull_requests(state="all"),
                provider.list_commits(),
                provider.list_branches(),
            )
            snapshot = {
                "repository": [(provider.full_name, asdict(repository))],
                "issue": [(str(item.number), asdict(item)) for item in issues],
                "pull_request": [(str(item.number), asdict(item)) for item in pull_requests],
                "commit": [(item.sha, asdict(item)) for item in commits],
                "branch": [(item.name, asdict(item)) for item in branches],
            }
            self.repo.replace_snapshot(snapshot)
            self.repo.set_sync_state("SUCCESS", succeeded=True)
            return self.get_overview(actor)
        except Exception as exc:
            self.repo.set_sync_state("FAILED", error=exc.__class__.__name__)
            raise

    def get_overview(self, actor: Actor) -> dict:
        self._ensure_enabled()
        repository = self.repo.list_items("repository", 1)
        issues = self.list_open_issues(actor)
        pull_requests = self.list_open_pull_requests(actor)
        sync = self.repo.get_sync_state()
        return {
            "repository": repository[0] if repository else None,
            "open_issues": len(issues),
            "open_pull_requests": len(pull_requests),
            "failing_checks": sum(1 for item in pull_requests if item.get("checks_status") == "FAILING"),
            "sync": sync,
            "stale": self._is_stale(sync),
        }

    def list_open_issues(self, actor: Actor) -> list[dict]:
        self._ensure_enabled()
        return [item for item in self._recent("issue", "updated_at") if item.get("state") == "OPEN"]

    def list_recent_issues(self, actor: Actor) -> list[dict]:
        self._ensure_enabled()
        return self._recent("issue", "updated_at")

    def get_issue(self, actor: Actor, number: int) -> dict:
        self._ensure_enabled()
        return self.repo.get_item("issue", str(int(number)))

    def search_issues(self, actor: Actor, query: str) -> list[dict]:
        self._ensure_enabled()
        return [item for item in self.repo.search_items(query, self.max_results) if item["item_type"] == "issue"]

    def list_open_pull_requests(self, actor: Actor) -> list[dict]:
        self._ensure_enabled()
        return [
            item
            for item in self._recent("pull_request", "updated_at")
            if item.get("state") == "OPEN"
        ]

    def list_recent_pull_requests(self, actor: Actor) -> list[dict]:
        self._ensure_enabled()
        return self._recent("pull_request", "updated_at")

    def get_pull_request(self, actor: Actor, number: int) -> dict:
        self._ensure_enabled()
        return self.repo.get_item("pull_request", str(int(number)))

    def list_recent_commits(self, actor: Actor) -> list[dict]:
        self._ensure_enabled()
        return self._recent("commit", "committed_at")

    def list_branches(self, actor: Actor) -> list[dict]:
        self._ensure_enabled()
        return sorted(
            self.repo.list_items("branch", self.max_results),
            key=lambda item: str(item.get("name") or "").lower(),
        )

    def search_context(self, actor: Actor, query: str) -> list[dict]:
        self._ensure_enabled()
        return self.repo.search_items(query, self.max_results)

    def get_linked_context(self, actor: Actor, *, entity_type: str, entity_id: int) -> list[dict]:
        links = self.list_links(actor, entity_type=entity_type, entity_id=entity_id)
        records: list[dict] = []
        for link in links[: self.max_results]:
            record = self.repo.get_item(link["external_type"], link["external_id"])
            record["item_type"] = link["external_type"]
            records.append(record)
        return records

    def create_link(
        self,
        actor: Actor,
        *,
        entity_type: str,
        entity_id: int,
        external_type: str,
        external_id: str,
    ) -> dict:
        ensure_can_manage_project(actor)
        entity_type = entity_type.strip().lower()
        external_type = external_type.strip().lower()
        if entity_type == "task":
            self.task_service.get_task(actor, entity_id)
        elif entity_type == "bug":
            self.bug_service.get_bug(actor, entity_id)
        else:
            raise InvalidInputError("GitHub links support task or bug records")
        cache_type = {"issue": "issue", "pull_request": "pull_request", "pr": "pull_request"}.get(external_type)
        if cache_type is None:
            raise InvalidInputError("GitHub links support issues or pull requests")
        try:
            normalized_external_id = str(int(external_id.strip()))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError("Use a valid GitHub issue or pull request number") from exc
        try:
            self.repo.get_item(cache_type, normalized_external_id)
        except NotFoundError as exc:
            raise InvalidInputError("The cached GitHub record does not exist") from exc
        try:
            return self.repo.create_link(
                entity_type=entity_type,
                entity_id=entity_id,
                external_type=cache_type,
                external_id=normalized_external_id,
                created_by=actor.user_id,
            )
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc

    def list_links(self, actor: Actor, *, entity_type: str | None = None, entity_id: int | None = None) -> list[dict]:
        if entity_type is None or entity_id is None:
            ensure_can_manage_project(actor)
        elif entity_type == "task":
            self.task_service.get_task(actor, entity_id)
        elif entity_type == "bug":
            self.bug_service.get_bug(actor, entity_id)
        else:
            raise InvalidInputError("GitHub links support task or bug records")
        return self.repo.list_links(entity_type, entity_id)

    def delete_link(self, actor: Actor, link_id: int) -> None:
        ensure_can_manage_project(actor)
        self.repo.delete_link(link_id)

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise GitHubConfigurationError("GitHub integration is not configured for this project")

    def _require_provider(self) -> GitHubProvider:
        self._ensure_enabled()
        if self.provider is None:
            raise GitHubConfigurationError("GitHub provider is unavailable")
        return self.provider

    def _is_stale(self, sync: dict) -> bool:
        if sync.get("status") != "SUCCESS" or not sync.get("last_synced_at"):
            return True
        try:
            synced_at = datetime.fromisoformat(str(sync["last_synced_at"]))
        except ValueError:
            return True
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=UTC)
        else:
            synced_at = synced_at.astimezone(UTC)
        return (datetime.now(UTC) - synced_at).total_seconds() > self.cache_ttl

    def _recent(self, item_type: str, timestamp_field: str) -> list[dict]:
        items = self.repo.list_items(item_type, self.max_results)
        return sorted(items, key=lambda item: str(item.get(timestamp_field) or ""), reverse=True)
