import re
from dataclasses import dataclass

import aiohttp

from cse_hq_bot.ai.base import RetrievedContextRecord
from cse_hq_bot.errors import (
    AIConfigurationError,
    AIProviderUnavailableError,
    AIRateLimitError,
)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

_EXPLICIT_WEB_PATTERN = re.compile(
    r"\b(?:search (?:the )?web|web search|research (?:the )?web|look it up|"
    r"search online|internet research|tìm trên mạng|tìm web|tra cứu(?: trên mạng)?|"
    r"nghiên cứu trên mạng)\b",
    re.IGNORECASE,
)
_FRESHNESS_PATTERN = re.compile(
    r"\b(?:latest|currently|current version|today|this week|recent(?:ly)?|"
    r"newest|up[- ]to[- ]date|mới nhất|hiện tại|hôm nay|tuần này|gần đây|"
    r"cập nhật mới)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WebResearchIntent:
    required: bool
    explicit: bool


class WebResearchIntentDetector:
    def detect(self, question: str) -> WebResearchIntent:
        text = " ".join(str(question or "").split())
        explicit = bool(_EXPLICIT_WEB_PATTERN.search(text))
        fresh = bool(_FRESHNESS_PATTERN.search(text))
        return WebResearchIntent(required=explicit or fresh, explicit=explicit)


class TavilyWebResearchService:
    def __init__(
        self,
        api_key: str | None,
        *,
        enabled: bool,
        max_results: int = 5,
        timeout_seconds: int = 12,
    ):
        self.api_key = (api_key or "").strip() or None
        self.enabled = bool(enabled)
        self.max_results = max(1, min(8, int(max_results)))
        self.timeout_seconds = max(1, int(timeout_seconds))

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    async def search(self, query: str) -> list[RetrievedContextRecord]:
        if not self.enabled:
            raise AIConfigurationError("Web research is disabled")
        if not self.api_key:
            raise AIConfigurationError("TAVILY_API_KEY is required for web research")

        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": self.max_results,
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
            "safe_search": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    TAVILY_SEARCH_URL,
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status in {401, 403, 432, 433}:
                        raise AIConfigurationError(
                            "Web research credentials or quota are unavailable"
                        )
                    if response.status == 429:
                        raise AIRateLimitError("Web research rate limit exceeded")
                    if response.status >= 500:
                        raise AIProviderUnavailableError(
                            "Web research provider is temporarily unavailable"
                        )
                    if response.status >= 400:
                        raise AIProviderUnavailableError(
                            f"Web research request failed with status {response.status}"
                        )
                    data = await response.json()
        except TimeoutError as exc:
            raise AIProviderUnavailableError("Web research request timed out") from exc
        except aiohttp.ClientError as exc:
            raise AIProviderUnavailableError(
                "Web research provider is temporarily unavailable"
            ) from exc

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return []

        records: list[RetrievedContextRecord] = []
        for index, item in enumerate(results[: self.max_results], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Web result").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()
            if not url or not snippet:
                continue
            records.append(
                RetrievedContextRecord(
                    source_type="web",
                    source_id=f"WEB-{index:03d}",
                    title=title[:180],
                    content=snippet[:1600],
                    timestamp=None,
                    retrieval_reason="fresh read-only web research",
                    url=url,
                )
            )
        return records
