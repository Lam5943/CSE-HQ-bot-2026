from cse_hq_bot.services.forum_publishing_service import ForumPublishingService


class GitHubEventService:
    def __init__(
        self,
        forum_service: ForumPublishingService,
        *,
        bug_label: str = "bug",
    ):
        self.forum_service = forum_service
        self.bug_label = bug_label.strip().casefold() or "bug"

    async def handle(self, event_type: str, payload: dict) -> str:
        if event_type == "pull_request":
            return await self._handle_pull_request(payload)
        if event_type == "issues":
            return await self._handle_issue(payload)
        if event_type == "release":
            return await self._handle_release(payload)
        if event_type in {"workflow_run", "check_run", "check_suite"}:
            return await self._handle_ci(event_type, payload)
        return "IGNORED_UNSUPPORTED_EVENT"

    async def _handle_pull_request(self, payload: dict) -> str:
        action = str(payload.get("action") or "").lower()
        if action not in {"opened", "reopened", "ready_for_review", "closed"}:
            return "IGNORED_UNSUPPORTED_ACTION"
        pull_request = self._mapping(payload.get("pull_request"))
        number = self._positive_number(payload.get("number") or pull_request.get("number"))
        if number is None:
            return "IGNORED_MALFORMED_EVENT"
        merged = bool(pull_request.get("merged"))
        state_label = "merged" if action == "closed" and merged else action
        title = f'PR #{number} — {pull_request.get("title") or "Untitled pull request"}'
        author = self._mapping(pull_request.get("user")).get("login") or "unknown"
        url = pull_request.get("html_url") or "No GitHub link available"
        content = "\n".join(
            (
                f"**State:** {state_label.replace('_', ' ').title()}",
                f"**Author:** {author}",
                f"**GitHub:** {url}",
                "",
                str(pull_request.get("body") or "No description."),
            )
        )
        result = await self.forum_service.publish_or_update(
            forum_kind="pull_request",
            entity_type="pull_request",
            entity_id=str(number),
            title=title,
            content=content,
            reply=f"GitHub pull request event: **{state_label.replace('_', ' ')}**.",
        )
        return result.outcome

    async def _handle_issue(self, payload: dict) -> str:
        action = str(payload.get("action") or "").lower()
        if action not in {"opened", "reopened", "closed", "edited", "labeled", "assigned"}:
            return "IGNORED_UNSUPPORTED_ACTION"
        issue = self._mapping(payload.get("issue"))
        if issue.get("pull_request") is not None:
            return "IGNORED_PULL_REQUEST_ISSUE"
        labels = {
            str(self._mapping(label).get("name") or "").strip().casefold()
            for label in issue.get("labels") or []
        }
        if self.bug_label not in labels:
            return "IGNORED_NOT_BUG"
        number = self._positive_number(issue.get("number") or payload.get("number"))
        if number is None:
            return "IGNORED_MALFORMED_EVENT"
        title = f'GH-ISSUE-{number} — {issue.get("title") or "Untitled issue"}'
        author = self._mapping(issue.get("user")).get("login") or "unknown"
        url = issue.get("html_url") or "No GitHub link available"
        content = "\n".join(
            (
                f"**State:** {str(issue.get('state') or action).title()}",
                f"**Author:** {author}",
                f"**GitHub:** {url}",
                "",
                str(issue.get("body") or "No description."),
            )
        )
        result = await self.forum_service.publish_or_update(
            forum_kind="bug",
            entity_type="github_issue",
            entity_id=str(number),
            title=title,
            content=content,
            reply=f"GitHub issue event: **{action}**.",
        )
        return result.outcome

    async def _handle_release(self, payload: dict) -> str:
        if str(payload.get("action") or "").lower() != "published":
            return "IGNORED_UNSUPPORTED_ACTION"
        release = self._mapping(payload.get("release"))
        release_id = self._positive_number(release.get("id"))
        if release_id is None:
            return "IGNORED_MALFORMED_EVENT"
        tag = str(release.get("tag_name") or "Release")
        name = str(release.get("name") or "Published release")
        title = f"{tag} — {name}"
        content = "\n".join(
            (
                "**State:** Published",
                f"**GitHub:** {release.get('html_url') or 'No GitHub link available'}",
                "",
                str(release.get("body") or "No release notes."),
            )
        )
        result = await self.forum_service.publish_or_update(
            forum_kind="release",
            entity_type="release",
            entity_id=str(release_id),
            title=title,
            content=content,
        )
        return result.outcome

    async def _handle_ci(self, event_type: str, payload: dict) -> str:
        record = self._mapping(payload.get(event_type))
        pull_requests = record.get("pull_requests") or []
        if not pull_requests:
            return "IGNORED_NO_PULL_REQUEST"
        number = self._positive_number(self._mapping(pull_requests[0]).get("number"))
        if number is None:
            return "IGNORED_MALFORMED_EVENT"
        name = record.get("name") or event_type
        conclusion = record.get("conclusion") or record.get("status") or "unknown"
        url = record.get("html_url") or ""
        content = f"CI update — **{name}**: `{conclusion}`."
        if url:
            content += f"\n{url}"
        result = await self.forum_service.reply_existing(
            entity_type="pull_request",
            entity_id=str(number),
            content=content,
        )
        return result.outcome

    def _mapping(self, value: object) -> dict:
        return value if isinstance(value, dict) else {}

    def _positive_number(self, value: object) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None
