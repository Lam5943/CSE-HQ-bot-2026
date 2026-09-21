import asyncio
import ipaddress
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

import aiohttp

from cse_hq_bot.errors import (
    WebResearchConfigurationError,
    WebResearchProviderError,
    WebResearchTimeoutError,
)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

_URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_NO_WEB_PATTERN = re.compile(
    r"\b(?:do not search|don't search|without web|offline only|"
    r"không cần web|không tìm web|đừng tìm web)\b",
    re.IGNORECASE,
)
_DEEP_PATTERN = re.compile(
    r"\b(?:deep research|research deeply|in-depth research|"
    r"nghiên cứu sâu|research kỹ|đào sâu)\b",
    re.IGNORECASE,
)
_SEARCH_PATTERN = re.compile(
    r"\b(?:research|search (?:the )?web|look up|browse the web|"
    r"tìm (?:trên )?web|tìm trên mạng|tra cứu|mới nhất|latest|"
    r"current|currently|hiện tại|recent|gần đây|today|hôm nay)\b",
    re.IGNORECASE,
)


class WebResearchMode(StrEnum):
    NONE = "none"
    SEARCH = "search"
    DEEP = "deep"
    EXTRACT = "extract"


@dataclass(frozen=True)
class WebResearchPlan:
    mode: WebResearchMode
    query: str
    urls: tuple[str, ...] = ()
    time_range: str | None = None


@dataclass(frozen=True)
class WebDocument:
    title: str
    url: str
    content: str
    score: float | None = None


class WebResearchPlanner:
    def plan(self, question: str) -> WebResearchPlan:
        clean = " ".join(question.strip().split())
        if not clean or _NO_WEB_PATTERN.search(clean):
            return WebResearchPlan(WebResearchMode.NONE, clean)

        urls = tuple(self._extract_urls(clean))
        if urls:
            return WebResearchPlan(
                WebResearchMode.EXTRACT,
                clean,
                urls=urls[:3],
            )

        if _DEEP_PATTERN.search(clean):
            return WebResearchPlan(
                WebResearchMode.DEEP,
                clean,
                time_range=self._time_range(clean),
            )

        if _SEARCH_PATTERN.search(clean):
            return WebResearchPlan(
                WebResearchMode.SEARCH,
                clean,
                time_range=self._time_range(clean),
            )

        return WebResearchPlan(WebResearchMode.NONE, clean)

    @staticmethod
    def _time_range(question: str) -> str | None:
        lowered = question.lower()
        if any(token in lowered for token in ("today", "hôm nay", "yesterday", "hôm qua")):
            return "day"
        if any(token in lowered for token in ("this week", "tuần này", "gần đây", "recent")):
            return "week"
        if any(token in lowered for token in ("this month", "tháng này", "latest", "mới nhất")):
            return "month"
        return None

    @staticmethod
    def _extract_urls(question: str) -> list[str]:
        urls: list[str] = []
        for match in _URL_PATTERN.findall(question):
            candidate = match.rstrip(".,);]}>")
            if _is_public_http_url(candidate):
                urls.append(candidate)
        return list(dict.fromkeys(urls))


class TavilyWebResearchService:
    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str | None,
        timeout_seconds: int = 15,
        max_results: int = 5,
    ):
        self.enabled = bool(enabled)
        self.api_key = api_key
        self.timeout_seconds = max(1, min(60, int(timeout_seconds)))
        self.max_results = max(1, min(8, int(max_results)))

        if self.enabled and not self.api_key:
            raise WebResearchConfigurationError(
                "TAVILY_API_KEY is required when WEB_RESEARCH_ENABLED=true"
            )

    async def research(
        self,
        plan: WebResearchPlan,
    ) -> list[WebDocument]:
        if not self.enabled or plan.mode == WebResearchMode.NONE:
            return []
        if plan.mode == WebResearchMode.EXTRACT:
            return await self._extract(plan)
        return await self._search(plan)

    async def _search(self, plan: WebResearchPlan) -> list[WebDocument]:
        payload: dict = {
            "query": plan.query,
            "search_depth": (
                "advanced" if plan.mode == WebResearchMode.DEEP else "basic"
            ),
            "max_results": self.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
        }
        if plan.mode == WebResearchMode.DEEP:
            payload["chunks_per_source"] = 2
        if plan.time_range:
            payload["time_range"] = plan.time_range

        response = await self._post(TAVILY_SEARCH_URL, payload)
        results = response.get("results")
        if not isinstance(results, list):
            raise WebResearchProviderError("Tavily search returned malformed results")

        documents: list[WebDocument] = []
        for item in results[: self.max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            content = str(item.get("content") or "").strip()
            if not url or not content or not _is_public_http_url(url):
                continue
            score = item.get("score")
            documents.append(
                WebDocument(
                    title=_clean_title(item.get("title"), url),
                    url=url,
                    content=_bounded_content(content),
                    score=float(score) if isinstance(score, (int, float)) else None,
                )
            )
        return documents

    async def _extract(self, plan: WebResearchPlan) -> list[WebDocument]:
        urls = [url for url in plan.urls[:3] if _is_public_http_url(url)]
        if not urls:
            return []

        response = await self._post(
            TAVILY_EXTRACT_URL,
            {
                "urls": urls,
                "extract_depth": "basic",
                "format": "markdown",
                "query": plan.query,
                "chunks_per_source": 3,
                "include_images": False,
                "include_usage": True,
            },
        )
        results = response.get("results")
        if not isinstance(results, list):
            raise WebResearchProviderError("Tavily extract returned malformed results")

        documents: list[WebDocument] = []
        for item in results[:3]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            content = str(item.get("raw_content") or "").strip()
            if not url or not content or not _is_public_http_url(url):
                continue
            documents.append(
                WebDocument(
                    title=_clean_title(item.get("title"), url),
                    url=url,
                    content=_bounded_content(content),
                )
            )
        return documents

    async def _post(self, url: str, payload: dict) -> dict:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status in {401, 403}:
                        raise WebResearchConfigurationError(
                            "Tavily credentials were rejected"
                        )
                    if response.status == 429:
                        raise WebResearchProviderError(
                            "Tavily research quota or rate limit was exceeded"
                        )
                    if response.status >= 500:
                        raise WebResearchProviderError(
                            "Tavily is temporarily unavailable"
                        )
                    if response.status >= 400:
                        raise WebResearchProviderError(
                            f"Tavily request failed with status {response.status}"
                        )
                    data = await response.json()
        except TimeoutError as exc:
            raise WebResearchTimeoutError("Web research timed out") from exc
        except aiohttp.ClientError as exc:
            raise WebResearchProviderError("Web research connection failed") from exc
        except asyncio.CancelledError:
            raise

        if not isinstance(data, dict):
            raise WebResearchProviderError("Tavily returned a malformed response")
        return data


def _clean_title(value: object, url: str) -> str:
    title = " ".join(str(value or "").split()).strip()
    if title:
        return title[:160]
    parsed = urlparse(url)
    return (parsed.hostname or "Web source")[:160]


def _bounded_content(value: str, limit: int = 3500) -> str:
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return f"{clean[: limit - 1]}…"


def _is_public_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
