import asyncio
from types import SimpleNamespace

import pytest

from cse_hq_bot.ai.base import AIProviderResponse
from cse_hq_bot.bot import CSEHQBot
from cse_hq_bot.db import Database
from cse_hq_bot.errors import (
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubNotFoundError,
    GitHubProviderError,
    GitHubRateLimitError,
    GitHubTimeoutError,
    InvalidInputError,
    PermissionDeniedError,
)
from cse_hq_bot.github_models import (
    GitHubBranch,
    GitHubCommit,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRepository,
)
from cse_hq_bot.github_provider import GitHubProvider
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.repositories.github_repository import GitHubRepositoryCache
from cse_hq_bot.services.ai_service import AIService
from cse_hq_bot.services.github_service import GitHubService
from cse_hq_bot.services.project_context_service import ProjectContextService
from cse_hq_bot.services.prompt_builder import PromptBuilder
from cse_hq_bot.services.report_service import ReportService
from cse_hq_bot.services.retrieval_planner import RetrievalPlanner
from cse_hq_bot.ui import (
    GitHubView,
    TasksView,
    build_github_detail_embed,
    build_github_items_embed,
    build_github_overview_embed,
)


def _issue(number: int, state: str = "open") -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": state,
        "user": {"login": "alice"},
        "assignees": [{"login": "bob"}],
        "labels": [{"name": "backend"}],
        "created_at": "2026-09-20T00:00:00Z",
        "updated_at": "2026-09-21T00:00:00Z",
        "closed_at": None,
        "html_url": f"https://github.test/issues/{number}",
    }


def _pull(number: int = 9) -> dict:
    return {
        "number": number,
        "title": "GitHub integration",
        "state": "open",
        "draft": False,
        "user": {"login": "alice"},
        "base": {"ref": "main"},
        "head": {"ref": "feature", "sha": "a" * 40},
        "created_at": "2026-09-20T00:00:00Z",
        "updated_at": "2026-09-21T00:00:00Z",
        "merged_at": None,
        "html_url": f"https://github.test/pull/{number}",
    }


def test_github_provider_normalizes_repository_and_issue_pagination():
    calls = []

    def transport(url, headers, timeout):
        calls.append((url, headers, timeout))
        if url.endswith("/repos/acme/project"):
            return {
                "name": "project",
                "owner": {"login": "acme"},
                "default_branch": "main",
                "description": "Demo",
                "visibility": "private",
                "updated_at": "2026-09-21T00:00:00Z",
                "html_url": "https://github.test/acme/project",
            }, {}
        if "page=2" in url:
            return [_issue(2)], {}
        if "/issues?" in url:
            pull_shaped_issue = {**_issue(9), "pull_request": {"url": "ignored"}}
            return [_issue(1), pull_shaped_issue], {
                "Link": '<https://api.github.com/repos/acme/project/issues?page=2>; rel="next"'
            }
        raise AssertionError(url)

    provider = GitHubProvider("acme", "project", "secret", max_results=5, transport=transport)
    repository = asyncio.run(provider.get_repository())
    issues = asyncio.run(provider.list_issues())

    assert repository.default_branch == "main"
    assert repository.visibility == "private"
    assert [item.number for item in issues] == [1, 2]
    assert all(call[1]["Authorization"] == "Bearer secret" for call in calls)
    assert all(call[2] == 15 for call in calls)


def test_github_provider_rejects_unsafe_pagination_url():
    def transport(url, headers, timeout):
        return [_issue(1)], {"Link": '<https://example.test/steal>; rel="next"'}

    provider = GitHubProvider("acme", "project", "secret", max_results=5, transport=transport)

    with pytest.raises(GitHubProviderError, match="unsafe pagination URL"):
        asyncio.run(provider.list_issues())


def test_github_provider_normalizes_pr_reviews_checks_commits_and_branches():
    def transport(url, headers, timeout):
        if "/pulls?" in url:
            return [_pull()], {}
        if url.endswith("/pulls/9/reviews?per_page=100"):
            return [
                {"user": {"login": "reviewer"}, "state": "APPROVED", "submitted_at": "now"},
                {"user": {"login": "other"}, "state": "CHANGES_REQUESTED", "submitted_at": "later"},
            ], {}
        if url.endswith(f"/commits/{'a' * 40}/check-runs"):
            return {"check_runs": [{"status": "completed", "conclusion": "failure"}]}, {}
        if "/commits?" in url:
            return [
                {
                    "sha": "b" * 40,
                    "commit": {"message": "First line\nbody", "author": {"name": "Alice", "date": "now"}},
                    "author": None,
                    "html_url": "https://github.test/commit/b",
                }
            ], {}
        if "/branches?" in url:
            return [{"name": "main", "protected": True, "commit": {"sha": "c" * 40}}], {}
        if url.endswith(f"/commits/{'c' * 40}"):
            return {"commit": {"committer": {"date": "2026-09-21T00:00:00Z"}}}, {}
        raise AssertionError(url)

    provider = GitHubProvider("acme", "project", "secret", transport=transport)
    pull_requests = asyncio.run(provider.list_pull_requests())
    commits = asyncio.run(provider.list_commits())
    branches = asyncio.run(provider.list_branches())

    assert pull_requests[0].review_status == "CHANGES_REQUESTED"
    assert pull_requests[0].checks_status == "FAILING"
    assert len(pull_requests[0].reviews) == 2
    assert commits[0].short_sha == "bbbbbbb"
    assert commits[0].message == "First line"
    assert branches[0].protected is True
    assert branches[0].latest_commit_time == "2026-09-21T00:00:00Z"


class _ProviderFailure(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_ProviderFailure("bad auth", 401), GitHubAuthenticationError),
        (_ProviderFailure("rate limit", 403), GitHubRateLimitError),
        (_ProviderFailure("too many requests", 429), GitHubRateLimitError),
        (_ProviderFailure("missing", 404), GitHubNotFoundError),
        (TimeoutError("slow"), GitHubTimeoutError),
        (RuntimeError("boom"), GitHubProviderError),
    ],
)
def test_github_provider_normalizes_failures(error, expected):
    def transport(url, headers, timeout):
        raise error

    provider = GitHubProvider("acme", "project", "secret", transport=transport)
    with pytest.raises(expected):
        asyncio.run(provider.get_repository())


def test_github_provider_rejects_missing_config_and_malformed_payload():
    with pytest.raises(GitHubConfigurationError):
        GitHubProvider(None, "project", "secret")

    provider = GitHubProvider("acme", "project", "secret", transport=lambda *_: ([], {}))
    with pytest.raises(GitHubProviderError, match="malformed"):
        asyncio.run(provider.get_repository())


class _TaskService:
    def get_task(self, actor, task_id):
        if task_id != 14:
            raise InvalidInputError("missing task")
        return {"id": task_id}


class _BugService:
    def get_bug(self, actor, bug_id):
        if bug_id != 4:
            raise InvalidInputError("missing bug")
        return {"id": bug_id}


class _FakeProvider:
    full_name = "acme/project"

    def __init__(self):
        self.fail = False
        self.issues = [
            GitHubIssue(12, "Open issue", "OPEN", "alice", (), (), "then", "now", None, "issue-url")
        ]

    async def get_repository(self):
        if self.fail:
            raise GitHubProviderError("offline")
        return GitHubRepository("acme", "project", "main", "Demo", "private", "now", "repo-url")

    async def list_issues(self, state="all"):
        return self.issues

    async def list_pull_requests(self, state="all"):
        return [
            GitHubPullRequest(
                9, "Open PR", "OPEN", False, "alice", "main", "feature", "a" * 40,
                "then", "now", None, "APPROVED", (), "FAILING", "pr-url"
            )
        ]

    async def list_commits(self):
        return [GitHubCommit("b" * 40, "bbbbbbb", "Change", "alice", "now", "commit-url")]

    async def list_branches(self):
        return [GitHubBranch("main", True, "b" * 40, "now")]


@pytest.fixture
def github_service(tmp_path):
    db = Database(str(tmp_path / "github.db"))
    db.initialize()
    provider = _FakeProvider()
    service = GitHubService(
        GitHubRepositoryCache(db),
        provider,
        _TaskService(),
        _BugService(),
        enabled=True,
        max_results=10,
    )
    return service, provider


def test_github_sync_cache_update_failure_preservation_and_metadata(github_service):
    service, provider = github_service
    leader = Actor("lead", Role.LEADER)

    overview = asyncio.run(service.sync_all(leader))
    assert overview["open_issues"] == 1
    assert overview["open_pull_requests"] == 1
    assert overview["failing_checks"] == 1
    assert overview["sync"]["status"] == "SUCCESS"
    last_synced = overview["sync"]["last_synced_at"]

    provider.fail = True
    with pytest.raises(GitHubProviderError):
        asyncio.run(service.sync_all(leader))

    assert service.list_open_issues(leader)[0]["number"] == 12
    state = service.repo.get_sync_state()
    assert state["status"] == "FAILED"
    assert state["last_synced_at"] == last_synced
    assert state["error"] == "GitHubProviderError"

    provider.fail = False
    provider.issues = []
    asyncio.run(service.sync_all(leader))
    assert service.list_recent_issues(leader) == []


def test_github_sync_and_links_enforce_permissions(github_service):
    service, _ = github_service
    leader = Actor("lead", Role.LEADER)
    member = Actor("member", Role.MEMBER)

    with pytest.raises(PermissionDeniedError):
        asyncio.run(service.sync_all(member))
    asyncio.run(service.sync_all(leader))

    task_link = service.create_link(
        leader,
        entity_type="task",
        entity_id=14,
        external_type="issue",
        external_id="12",
    )
    pr_link = service.create_link(
        leader,
        entity_type="bug",
        entity_id=4,
        external_type="pr",
        external_id="9",
    )
    assert task_link["external_type"] == "issue"
    assert pr_link["external_type"] == "pull_request"
    assert service.list_links(leader, entity_type="task", entity_id=14) == [task_link]
    assert service.get_linked_context(leader, entity_type="task", entity_id=14)[0]["number"] == 12

    with pytest.raises(InvalidInputError, match="already exists"):
        service.create_link(
            leader,
            entity_type="task",
            entity_id=14,
            external_type="issue",
            external_id="12",
        )
    with pytest.raises(PermissionDeniedError):
        service.create_link(
            member,
            entity_type="task",
            entity_id=14,
            external_type="issue",
            external_id="12",
        )
    with pytest.raises(InvalidInputError, match="does not exist"):
        service.create_link(
            leader,
            entity_type="bug",
            entity_id=4,
            external_type="issue",
            external_id="999",
        )
    with pytest.raises(PermissionDeniedError):
        service.delete_link(member, pr_link["id"])
    service.delete_link(leader, pr_link["id"])
    assert service.list_links(leader, entity_type="bug", entity_id=4) == []


def test_disabled_github_service_fails_without_affecting_other_services(tmp_path):
    db = Database(str(tmp_path / "disabled.db"))
    db.initialize()
    service = GitHubService(
        GitHubRepositoryCache(db),
        None,
        _TaskService(),
        _BugService(),
        enabled=False,
        max_results=10,
    )
    with pytest.raises(GitHubConfigurationError, match="not configured"):
        service.get_overview(Actor("member", Role.MEMBER))


class _RecordingAIProvider:
    def __init__(self):
        self.records = []

    async def generate(self, *, system_instruction, messages, context_records, timeout_seconds):
        self.records = context_records
        return AIProviderResponse(text=f"{context_records[0].source_id} is relevant.")


class _GitHubContext:
    def get_open_pull_requests(self, actor):
        return [
            {
                "number": 9,
                "title": "Open PR",
                "state": "OPEN",
                "draft": False,
                "review_status": "APPROVED",
                "checks_status": "FAILING",
                "base_branch": "main",
                "head_branch": "feature",
                "updated_at": "now",
                "url": "pr-url",
            }
        ]

    def get_linked_github_context(self, actor, *, entity_type, entity_id):
        assert (entity_type, entity_id) == ("task", 14)
        return [
            {
                "item_type": "issue",
                "number": 12,
                "title": "Linked issue",
                "state": "OPEN",
                "author": "alice",
                "assignees": [],
                "labels": [],
                "updated_at": "now",
                "url": "issue-url",
            }
        ]


def test_github_retrieval_planner_and_grounding_sources():
    provider = _RecordingAIProvider()
    ai_service = AIService(
        provider,
        _GitHubContext(),
        RetrievalPlanner(),
        PromptBuilder(),
        max_context_items=5,
        request_timeout=5,
    )
    answer = asyncio.run(
        ai_service.answer_question(
            actor=Actor("member", Role.MEMBER),
            question="Which PRs are failing CI?",
            history_messages=[],
        )
    )

    assert answer.retrieval_strategy == "github_failing_checks"
    assert answer.source_refs == ["GH-PR-9"]
    assert provider.records[0].source_id == "GH-PR-9"

    linked_answer = asyncio.run(
        ai_service.answer_question(
            actor=Actor("member", Role.MEMBER),
            question="What issue is linked to TASK-014?",
            history_messages=[],
        )
    )
    assert linked_answer.retrieval_strategy == "github_links"
    assert linked_answer.source_refs == ["GH-ISSUE-12"]


@pytest.mark.parametrize(
    ("question", "strategy"),
    [
        ("What PRs are open?", "github_pull_requests"),
        ("What issues are still open?", "github_issues"),
        ("What changed in the repo recently?", "github_recent_commits"),
        ("Show GH-PR-9", "github_pr_context"),
    ],
)
def test_github_retrieval_routes(question, strategy):
    assert RetrievalPlanner().plan(question).strategy == strategy


def test_github_ui_embeds_show_cache_and_pagination():
    overview = build_github_overview_embed(
        {
            "repository": {"owner": "acme", "name": "project"},
            "open_issues": 8,
            "open_pull_requests": 2,
            "failing_checks": 1,
            "sync": {"status": "SUCCESS", "last_synced_at": "now"},
            "stale": False,
        }
    )
    normalized_issues = [
        {
            **_issue(index),
            "state": "OPEN",
            "assignees": ["bob"],
            "labels": ["backend"],
            "url": f"https://github.test/issues/{index}",
        }
        for index in range(1, 10)
    ]
    issues = build_github_items_embed("issues", normalized_issues, page=1)
    issue_detail = build_github_detail_embed("issues", normalized_issues[0])
    pull_detail = build_github_detail_embed(
        "pull_requests",
        {
            **_pull(),
            "state": "OPEN",
            "review_status": "APPROVED",
            "checks_status": "PASSING",
            "base_branch": "main",
            "head_branch": "feature",
            "url": "https://github.test/pull/9",
        },
    )

    assert "acme/project" in overview.description
    assert overview.fields[3].value == "SUCCESS • FRESH • Last synced: now"
    assert "#9" in issues.description
    assert issues.footer.text == "CSE-HQ • GitHub • Cached data • Page 2/2"
    assert issue_detail.title == "🐙 CSE-HQ • Issue #1"
    assert pull_detail.fields[2].value == "APPROVED"
    assert pull_detail.fields[3].value == "PASSING"


def test_github_view_does_not_capture_task_action_buttons():
    assert hasattr(TasksView, "start_task")
    assert hasattr(TasksView, "complete_task")
    assert not hasattr(GitHubView, "start_task")
    assert not hasattr(GitHubView, "complete_task")


def test_github_command_is_registered():
    bot = CSEHQBot(SimpleNamespace())

    async def runner():
        await bot.setup_hook()
        assert bot.tree.get_command("github") is not None
        await bot.close()

    asyncio.run(runner())


class _ContextGitHubService:
    enabled = True

    def get_overview(self, actor):
        return {"repository": {"name": "project"}}

    def list_open_issues(self, actor):
        return [{"number": index} for index in range(20)]

    def list_open_pull_requests(self, actor):
        return [{"number": index} for index in range(20)]

    def list_recent_commits(self, actor):
        return [{"short_sha": str(index)} for index in range(20)]

    def get_pull_request(self, actor, number):
        return {"number": number}

    def search_context(self, actor, query):
        return [{"number": index} for index in range(20)]


def test_project_context_exposes_bounded_github_reads_and_disabled_behavior():
    actor = Actor("member", Role.MEMBER)
    context = ProjectContextService(None, None, None, None, None, None, None, _ContextGitHubService())

    assert context.get_github_overview(actor)["enabled"] is True
    assert len(context.get_open_github_issues(actor)) == 10
    assert len(context.get_open_pull_requests(actor)) == 10
    assert len(context.get_recent_commits(actor)) == 10
    assert context.get_pr_context(actor, 9)["number"] == 9
    assert len(context.search_github_context(actor, "query")) == 10

    disabled = ProjectContextService(None, None, None, None, None, None, None)
    assert disabled.get_github_overview(actor)["enabled"] is False
    assert disabled.get_open_github_issues(actor) == []


class _ReportProject:
    def get_dashboard(self):
        return SimpleNamespace(name="CSE-HQ", task_done=2, task_total=3, bug_open=1, bug_total=2)


class _ReportStandups:
    def weekly_standup_summary(self):
        return {"entries": 1, "members": ["member"], "blockers": []}


def test_weekly_report_adds_cached_github_development_section(github_service):
    service, _ = github_service
    asyncio.run(service.sync_all(Actor("lead", Role.LEADER)))
    report = ReportService(
        _ReportProject(),
        _TaskService(),
        _BugService(),
        _ReportStandups(),
        service,
    ).weekly_progress_report()

    assert "Development" in report
    assert "PRs open: 1" in report
    assert "CI failures: 1" in report
