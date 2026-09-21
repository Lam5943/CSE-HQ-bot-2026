import asyncio
from types import SimpleNamespace

from cse_hq_bot.ai.base import AIProviderResponse, RetrievedContextRecord
from cse_hq_bot.bot import CSEHQBot, strip_bot_mention
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.services.ai_service import AIService
from cse_hq_bot.services.prompt_builder import PromptBuilder
from cse_hq_bot.services.retrieval_planner import RetrievalPlanner
from cse_hq_bot.services.web_research_service import (
    TavilyWebResearchService,
    WebResearchIntentDetector,
)
from cse_hq_bot.ui import build_welcome_embed


class RecordingProvider:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return AIProviderResponse(
            text=self.text,
            provider="fake",
            model="test",
        )


class MinimalProjectContext:
    def search_project_memory(self, actor, query, domains=None, limit=6):
        return []

    def search_github_context(self, actor, query):
        return []

    def get_current_work(self, actor):
        return {
            "active_tasks": [],
            "blocked_tasks": [],
            "current_standup": None,
            "open_bugs": [],
        }


class FakeWebResearch:
    configured = True

    def __init__(self):
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return [
            RetrievedContextRecord(
                source_type="web",
                source_id="WEB-001",
                title="Qwen release notes",
                content="Qwen 3.8 is the current release in this test.",
                timestamp=None,
                retrieval_reason="fresh read-only web research",
                url="https://example.com/qwen",
            )
        ]


def build_ai_service(provider, web_research=None):
    return AIService(
        provider,
        MinimalProjectContext(),
        RetrievalPlanner(),
        PromptBuilder(),
        max_context_items=4,
        request_timeout=5,
        web_research_service=web_research,
    )


def test_strip_bot_mention_handles_discord_mention_variants():
    assert strip_bot_mention("<@123> hello", 123) == "hello"
    assert strip_bot_mention("hey <@!123> research this", 123) == "hey  research this"
    assert strip_bot_mention("<@999> keep this", 123) == "<@999> keep this"


def test_public_mentions_are_read_only_for_project_mutations():
    provider = RecordingProvider("must not run")
    service = build_ai_service(provider)

    answer = asyncio.run(
        service.answer_question(
            actor=Actor("member-1", Role.MEMBER),
            question="Create a task called ship the demo",
            history_messages=[],
            allow_actions=False,
        )
    )

    assert answer.retrieval_strategy == "public_mutation_rejected"
    assert "/ai" in answer.content
    assert provider.calls == []


def test_explicit_web_research_is_grounded_and_exposes_sources():
    provider = RecordingProvider("The current model is documented here [WEB-001].")
    web = FakeWebResearch()
    service = build_ai_service(provider, web)

    answer = asyncio.run(
        service.answer_question(
            actor=Actor("member-1", Role.MEMBER),
            question="Search the web for the latest Qwen release",
            history_messages=[],
            allow_actions=False,
        )
    )

    assert web.queries == ["Search the web for the latest Qwen release"]
    assert answer.retrieval_strategy.endswith("+web")
    assert answer.source_refs == ["WEB-001"]
    assert "https://example.com/qwen" in answer.content
    call = provider.calls[0]
    assert any(record.source_id == "WEB-001" for record in call["context_records"])
    assert "Fresh web-search records are included" in call["system_instruction"]


def test_project_current_work_does_not_trigger_auto_web_research():
    provider = RecordingProvider("No active work right now.")
    web = FakeWebResearch()
    service = build_ai_service(provider, web)

    answer = asyncio.run(
        service.answer_question(
            actor=Actor("member-1", Role.MEMBER),
            question="What am I currently working on?",
            history_messages=[],
        )
    )

    assert answer.retrieval_strategy == "current_work"
    assert web.queries == []


def test_web_research_intent_detects_explicit_and_fresh_queries():
    detector = WebResearchIntentDetector()

    explicit = detector.detect("bro search the web for Python 3.15")
    fresh = detector.detect("what is the latest Python version?")
    ordinary = detector.detect("explain database normalization")

    assert explicit.required is True
    assert explicit.explicit is True
    assert fresh.required is True
    assert fresh.explicit is False
    assert ordinary.required is False


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return {
            "results": [
                {
                    "title": "Example result",
                    "url": "https://example.com/result",
                    "content": "Fresh result snippet",
                    "score": 0.9,
                }
            ]
        }


class FakeSession:
    def __init__(self):
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, json, headers):
        self.post_calls.append((url, json, headers))
        return FakeResponse()


def test_tavily_search_is_bounded_read_only_retrieval(monkeypatch):
    from cse_hq_bot.services import web_research_service as module

    fake_session = FakeSession()
    monkeypatch.setattr(
        module.aiohttp,
        "ClientSession",
        lambda **kwargs: fake_session,
    )
    service = TavilyWebResearchService(
        "tvly-test-key",
        enabled=True,
        max_results=4,
        timeout_seconds=5,
    )

    records = asyncio.run(service.search("latest AI news"))

    assert len(records) == 1
    assert records[0].source_id == "WEB-001"
    assert records[0].url == "https://example.com/result"
    assert records[0].content == "Fresh result snippet"
    _, payload, headers = fake_session.post_calls[0]
    assert payload["include_raw_content"] is False
    assert payload["include_images"] is False
    assert payload["max_results"] == 4
    assert headers["Authorization"] == "Bearer tvly-test-key"


def test_welcome_feature_requests_member_intent_only_when_enabled():
    disabled = CSEHQBot(SimpleNamespace(), welcome_enabled=False)
    enabled = CSEHQBot(SimpleNamespace(), welcome_enabled=True)

    assert disabled.intents.members is False
    assert enabled.intents.members is True


def test_welcome_embed_matches_cse_hq_design_system():
    embed = build_welcome_embed(
        member_mention="<@123>",
        display_name="Alice",
        guild_name="Explore CompSci 2026",
    )

    assert embed.title == "👋 CSE-HQ • Welcome"
    assert "Alice" in (embed.description or "")
    assert "Explore CompSci 2026" in (embed.description or "")
    assert "/dashboard" in embed.fields[0].value
    assert embed.footer.text == "CSE-HQ • Welcome • Glad you're here"
