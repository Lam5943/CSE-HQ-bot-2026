import asyncio
import json
import socket
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from cse_hq_bot.errors import (
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubNotFoundError,
    GitHubProviderError,
    GitHubRateLimitError,
    GitHubTimeoutError,
)
from cse_hq_bot.github_models import (
    GitHubBranch,
    GitHubCommit,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRepository,
)

Transport = Callable[[str, dict[str, str], int], tuple[object, dict[str, str]]]


class GitHubProvider:
    API_ROOT = "https://api.github.com"

    def __init__(
        self,
        owner: str | None,
        repository: str | None,
        token: str | None,
        *,
        timeout_seconds: int = 15,
        max_results: int = 30,
        transport: Transport | None = None,
    ):
        if not owner or not repository or not token:
            raise GitHubConfigurationError(
                "GITHUB_REPOSITORY_OWNER, GITHUB_REPOSITORY_NAME, and GITHUB_TOKEN are required"
            )
        self.owner = owner
        self.repository = repository
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_results = max(1, min(int(max_results), 100))
        self._headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "cse-hq-bot",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._transport = transport or self._http_get

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repository}"

    async def get_repository(self) -> GitHubRepository:
        payload, _ = await self._request(f"/repos/{self.full_name}")
        data = self._expect_dict(payload)
        owner = self._expect_dict(data.get("owner", {})).get("login") or self.owner
        return GitHubRepository(
            owner=str(owner),
            name=str(data.get("name") or self.repository),
            default_branch=str(data.get("default_branch") or ""),
            description=str(data.get("description") or ""),
            visibility=str(data.get("visibility") or ("private" if data.get("private") else "public")),
            updated_at=data.get("updated_at"),
            url=str(data.get("html_url") or ""),
        )

    async def list_issues(self, *, state: str = "all") -> list[GitHubIssue]:
        rows = await self._paginate(
            f"/repos/{self.full_name}/issues",
            {"state": state, "sort": "updated", "direction": "desc", "per_page": "100"},
        )
        return [self._issue(row) for row in rows if not row.get("pull_request")]

    async def get_issue(self, number: int) -> GitHubIssue:
        payload, _ = await self._request(f"/repos/{self.full_name}/issues/{int(number)}")
        data = self._expect_dict(payload)
        if data.get("pull_request"):
            raise GitHubNotFoundError(f"GitHub issue #{number} was not found")
        return self._issue(data)

    async def list_pull_requests(self, *, state: str = "all") -> list[GitHubPullRequest]:
        rows = await self._paginate(
            f"/repos/{self.full_name}/pulls",
            {"state": state, "sort": "updated", "direction": "desc", "per_page": "100"},
        )
        results: list[GitHubPullRequest] = []
        for row in rows:
            number = int(row.get("number") or 0)
            reviews, checks = await asyncio.gather(
                self._list_reviews(number),
                self._get_checks(self._expect_dict(row.get("head", {})).get("sha")),
            )
            results.append(self._pull_request(row, reviews, checks))
        return results

    async def get_pull_request(self, number: int) -> GitHubPullRequest:
        payload, _ = await self._request(f"/repos/{self.full_name}/pulls/{int(number)}")
        data = self._expect_dict(payload)
        head_sha = self._expect_dict(data.get("head", {})).get("sha")
        reviews, checks = await asyncio.gather(
            self._list_reviews(number),
            self._get_checks(head_sha),
        )
        return self._pull_request(data, reviews, checks)

    async def list_commits(self) -> list[GitHubCommit]:
        rows = await self._paginate(
            f"/repos/{self.full_name}/commits",
            {"per_page": "100"},
        )
        return [self._commit(row) for row in rows]

    async def list_branches(self) -> list[GitHubBranch]:
        rows = await self._paginate(
            f"/repos/{self.full_name}/branches",
            {"per_page": "100"},
        )
        async def normalize(row: dict) -> GitHubBranch:
            commit = self._expect_dict(row.get("commit", {}))
            sha = str(commit.get("sha") or "")
            latest_commit_time = None
            if sha:
                payload, _ = await self._request(f"/repos/{self.full_name}/commits/{sha}")
                commit_data = self._expect_dict(self._expect_dict(payload).get("commit", {}))
                committer = self._expect_dict(commit_data.get("committer", {}))
                latest_commit_time = committer.get("date")
            return GitHubBranch(
                name=str(row.get("name") or ""),
                protected=bool(row.get("protected")),
                latest_sha=sha,
                latest_commit_time=latest_commit_time,
            )

        return list(await asyncio.gather(*(normalize(row) for row in rows)))

    async def _list_reviews(self, number: int) -> list[dict]:
        rows = await self._paginate(
            f"/repos/{self.full_name}/pulls/{number}/reviews",
            {"per_page": "100"},
        )
        return [
            {
                "author": str(self._expect_dict(row.get("user", {})).get("login") or ""),
                "state": str(row.get("state") or "").upper(),
                "submitted_at": row.get("submitted_at"),
            }
            for row in rows
        ]

    async def _get_checks(self, sha: object) -> str:
        if not sha:
            return "UNKNOWN"
        payload, _ = await self._request(f"/repos/{self.full_name}/commits/{sha}/check-runs")
        data = self._expect_dict(payload)
        runs = data.get("check_runs") or []
        if not isinstance(runs, list) or not runs:
            return "UNKNOWN"
        conclusions = {str(run.get("conclusion") or "").lower() for run in runs}
        statuses = {str(run.get("status") or "").lower() for run in runs}
        if statuses - {"completed"}:
            return "PENDING"
        if conclusions & {"failure", "timed_out", "action_required", "startup_failure"}:
            return "FAILING"
        if conclusions & {"cancelled", "skipped", "stale"}:
            return "CANCELLED"
        if conclusions and conclusions <= {"success", "neutral"}:
            return "PASSING"
        return "UNKNOWN"

    async def _paginate(self, path: str, params: dict[str, str]) -> list[dict]:
        url = self._url(path, params)
        rows: list[dict] = []
        while url and len(rows) < self.max_results:
            payload, headers = await self._request_url(url)
            page = self._expect_list(payload)
            rows.extend(self._expect_dict(item) for item in page)
            url = self._next_link(headers.get("Link") or headers.get("link") or "")
        return rows[: self.max_results]

    async def _request(self, path: str) -> tuple[object, dict[str, str]]:
        return await self._request_url(self._url(path))

    async def _request_url(self, url: str) -> tuple[object, dict[str, str]]:
        self._validate_api_url(url)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._transport, url, self._headers, self.timeout_seconds),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise GitHubTimeoutError("GitHub request timed out") from exc
        except GitHubProviderError:
            raise
        except Exception as exc:
            raise self._normalize_error(exc) from exc

    def _http_get(self, url: str, headers: dict[str, str], timeout: int) -> tuple[object, dict[str, str]]:
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                return payload, dict(response.headers.items())
        except (TimeoutError, HTTPError, URLError) as exc:
            raise self._normalize_error(exc) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubProviderError("GitHub returned a malformed response") from exc

    def _normalize_error(self, exc: Exception) -> GitHubProviderError:
        code = getattr(exc, "code", None)
        message = str(exc).lower()
        headers = getattr(exc, "headers", {}) or {}
        rate_limit_remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        if code == 401:
            return GitHubAuthenticationError("GitHub credentials were rejected")
        if code == 429 or (
            code == 403
            and (
                "rate" in message
                or "limit" in message
                or str(rate_limit_remaining) == "0"
                or bool(headers.get("Retry-After") or headers.get("retry-after"))
            )
        ):
            return GitHubRateLimitError("GitHub API rate limit exceeded")
        if code == 403:
            return GitHubAuthenticationError("GitHub access was denied")
        if code == 404:
            return GitHubNotFoundError("GitHub resource was not found")
        if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in message:
            return GitHubTimeoutError("GitHub request timed out")
        return GitHubProviderError("GitHub request failed")

    def _issue(self, data: dict) -> GitHubIssue:
        return GitHubIssue(
            number=int(data.get("number") or 0),
            title=str(data.get("title") or ""),
            state=str(data.get("state") or "unknown").upper(),
            author=str(self._expect_dict(data.get("user", {})).get("login") or ""),
            assignees=tuple(
                str(self._expect_dict(item).get("login") or "")
                for item in (data.get("assignees") or [])
            ),
            labels=tuple(
                str(self._expect_dict(item).get("name") or "")
                for item in (data.get("labels") or [])
            ),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            closed_at=data.get("closed_at"),
            url=str(data.get("html_url") or ""),
        )

    def _pull_request(self, data: dict, reviews: list[dict], checks: str) -> GitHubPullRequest:
        review_states = {item["state"] for item in reviews}
        if "CHANGES_REQUESTED" in review_states:
            review_status = "CHANGES_REQUESTED"
        elif "APPROVED" in review_states:
            review_status = "APPROVED"
        elif reviews:
            review_status = "REVIEW_REQUIRED"
        else:
            review_status = "NO_REVIEWS"
        head = self._expect_dict(data.get("head", {}))
        return GitHubPullRequest(
            number=int(data.get("number") or 0),
            title=str(data.get("title") or ""),
            state=str(data.get("state") or "unknown").upper(),
            draft=bool(data.get("draft")),
            author=str(self._expect_dict(data.get("user", {})).get("login") or ""),
            base_branch=str(self._expect_dict(data.get("base", {})).get("ref") or ""),
            head_branch=str(head.get("ref") or ""),
            head_sha=str(head.get("sha") or ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            merged_at=data.get("merged_at"),
            review_status=review_status,
            reviews=tuple(reviews),
            checks_status=checks,
            url=str(data.get("html_url") or ""),
        )

    def _commit(self, data: dict) -> GitHubCommit:
        commit = self._expect_dict(data.get("commit", {}))
        author_data = self._expect_dict(commit.get("author", {}))
        return GitHubCommit(
            sha=str(data.get("sha") or ""),
            short_sha=str(data.get("sha") or "")[:7],
            message=str(commit.get("message") or "").splitlines()[0],
            author=str(
                (data.get("author") if isinstance(data.get("author"), dict) else {}).get("login")
                or author_data.get("name")
                or ""
            ),
            committed_at=author_data.get("date"),
            url=str(data.get("html_url") or ""),
        )

    def _url(self, path: str, params: dict[str, str] | None = None) -> str:
        query = f"?{urlencode(params)}" if params else ""
        return f"{self.API_ROOT}{path}{query}"

    def _validate_api_url(self, url: str) -> None:
        parsed = urlparse(url)
        api_root = urlparse(self.API_ROOT)
        if parsed.scheme != "https" or parsed.netloc != api_root.netloc:
            raise GitHubProviderError("GitHub returned an unsafe pagination URL")

    def _next_link(self, value: str) -> str | None:
        for part in value.split(","):
            if 'rel="next"' in part:
                return part.split(";", 1)[0].strip().strip("<>")
        return None

    def _expect_dict(self, value: object) -> dict:
        if not isinstance(value, dict):
            raise GitHubProviderError("GitHub returned a malformed response")
        return value

    def _expect_list(self, value: object) -> list:
        if not isinstance(value, list):
            raise GitHubProviderError("GitHub returned a malformed response")
        return value
