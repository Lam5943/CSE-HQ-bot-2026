from dataclasses import dataclass


@dataclass(frozen=True)
class GitHubRepository:
    owner: str
    name: str
    default_branch: str
    description: str
    visibility: str
    updated_at: str | None
    url: str


@dataclass(frozen=True)
class GitHubIssue:
    number: int
    title: str
    state: str
    author: str
    assignees: tuple[str, ...]
    labels: tuple[str, ...]
    created_at: str | None
    updated_at: str | None
    closed_at: str | None
    url: str


@dataclass(frozen=True)
class GitHubPullRequest:
    number: int
    title: str
    state: str
    draft: bool
    author: str
    base_branch: str
    head_branch: str
    head_sha: str
    created_at: str | None
    updated_at: str | None
    merged_at: str | None
    review_status: str
    reviews: tuple[dict, ...]
    checks_status: str
    url: str


@dataclass(frozen=True)
class GitHubCommit:
    sha: str
    short_sha: str
    message: str
    author: str
    committed_at: str | None
    url: str


@dataclass(frozen=True)
class GitHubBranch:
    name: str
    protected: bool
    latest_sha: str
    latest_commit_time: str | None
