import asyncio
from types import SimpleNamespace

from cse_hq_bot.ai.base import AIProviderResponse, RetrievedContextRecord
from cse_hq_bot.bot import CSEHQBot, strip_bot_mention
from cse_hq_bot.config import load_config
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.services.ai_service import AIService, GroundedAnswer
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


class SequenceProvider:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        text = self.responses.pop(0)
        return AIProviderResponse(
            text=text,
            provider="fake",
            model="test",
        )


def test_chinese_drift_retries_once_in_user_language():
    provider = SequenceProvider(
        [
            "这是一个明显错误的中文回答，包含足够多的汉字来触发语言纠正。",
            "Bro, câu trả lời đúng phải bằng tiếng Việt.",
        ]
    )
    service = build_ai_service(provider)

    answer = asyncio.run(
        service.answer_question(
            actor=Actor("member-1", Role.MEMBER),
            question="bro giải thích normalization giúp mình",
            history_messages=[],
        )
    )

    assert answer.content == "Bro, câu trả lời đúng phải bằng tiếng Việt."
    assert len(provider.calls) == 2
    assert "<LANGUAGE_CORRECTION>" in provider.calls[1]["system_instruction"]


def test_explicit_chinese_request_does_not_trigger_language_retry():
    provider = SequenceProvider(
        ["这是按照用户要求提供的中文回答，内容足够长而且应该保持中文。"]
    )
    service = build_ai_service(provider)

    answer = asyncio.run(
        service.answer_question(
            actor=Actor("member-1", Role.MEMBER),
            question="Please answer this in Chinese",
            history_messages=[],
        )
    )

    assert "中文回答" in answer.content
    assert len(provider.calls) == 1


def test_small_chinese_quote_does_not_trigger_language_retry():
    assert AIService._should_correct_chinese_response(
        "bro từ 你好 nghĩa là gì?",
        "Trong tiếng Trung, 你好 nghĩa là xin chào.",
    ) is False



class EmptyWebResearch:
    configured = True

    async def search(self, query):
        return []


def test_empty_live_search_does_not_fall_back_to_stale_model_knowledge():
    provider = RecordingProvider("I will guess anyway")
    service = build_ai_service(provider, EmptyWebResearch())

    answer = asyncio.run(
        service.answer_question(
            actor=Actor("member-1", Role.MEMBER),
            question="Search the web for the latest obscure release",
            history_messages=[],
        )
    )

    assert answer.retrieval_strategy == "web_research_empty"
    assert "can't verify" in answer.content
    assert provider.calls == []


def test_load_config_reads_welcome_and_web_research_settings(monkeypatch):
    monkeypatch.setenv("WELCOME_ENABLED", "true")
    monkeypatch.setenv("WELCOME_CHANNEL_ID", "123456")
    monkeypatch.setenv("WEB_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("WEB_RESEARCH_MAX_RESULTS", "7")
    monkeypatch.setenv("WEB_RESEARCH_TIMEOUT", "9")

    config = load_config()

    assert config.welcome_enabled is True
    assert config.welcome_channel_id == "123456"
    assert config.web_research_enabled is True
    assert config.tavily_api_key == "tvly-test"
    assert config.web_research_max_results == 7
    assert config.web_research_timeout == 9


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


class FakeMentionAIService:
    def __init__(self):
        self.kwargs = None

    async def answer_question(self, **kwargs):
        self.kwargs = kwargs
        return GroundedAnswer(
            content="Mention reply",
            source_refs=[],
            invalid_source_refs=[],
            retrieval_strategy="fallback_search",
        )


class NoSessionService:
    def get_session_by_thread_id(self, thread_id):
        return None


class FakeMentionChannel:
    id = 777

    def __init__(self):
        self.sent = []

    async def send(self, content):
        self.sent.append(content)


class FakeMentionMessage:
    def __init__(self, bot_user):
        self.author = SimpleNamespace(
            bot=False,
            id=123,
            name="alice",
            display_name="Alice",
        )
        self.channel = FakeMentionChannel()
        self.content = f"<@{bot_user.id}> explain normalization"
        self.mentions = [bot_user]
        self.attachments = []
        self.guild = None
        self.replies = []

    async def reply(self, content, *, mention_author=False):
        self.replies.append((content, mention_author))


def test_direct_mention_flows_through_stateless_read_only_ai():
    ai_service = FakeMentionAIService()
    bot = CSEHQBot(
        SimpleNamespace(
            ai_session_service=NoSessionService(),
            ai_service=ai_service,
        )
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    bot._connection.user = bot_user
    message = FakeMentionMessage(bot_user)

    asyncio.run(bot.on_message(message))

    assert ai_service.kwargs["question"] == "explain normalization"
    assert ai_service.kwargs["history_messages"] == []
    assert ai_service.kwargs["allow_actions"] is False
    assert message.replies == [("Mention reply", False)]


class FakeWelcomeChannel:
    def __init__(self):
        self.calls = []

    async def send(self, **kwargs):
        self.calls.append(kwargs)


def test_member_join_sends_branded_welcome(monkeypatch):
    bot = CSEHQBot(SimpleNamespace(), welcome_enabled=True)
    channel = FakeWelcomeChannel()
    monkeypatch.setattr(bot, "_resolve_welcome_channel", lambda member: channel)
    member = SimpleNamespace(
        bot=False,
        id=321,
        mention="<@321>",
        display_name="Newbie",
        guild=SimpleNamespace(id=654, name="Explore CompSci 2026"),
    )

    asyncio.run(bot.on_member_join(member))

    assert len(channel.calls) == 1
    call = channel.calls[0]
    assert call["content"] == "<@321>"
    assert call["embed"].title == "👋 CSE-HQ • Welcome"
    assert "Newbie" in (call["embed"].description or "")


class FakeImageAttachment:
    filename = "idea.png"
    content_type = "image/png"

    def __init__(self):
        self._data = b"\x89PNG\r\n\x1a\nvision-context"
        self.size = len(self._data)

    async def read(self, *, use_cached=False):
        return self._data


class FakeReplyMessage:
    def __init__(
        self,
        *,
        message_id,
        author,
        channel,
        content="",
        mentions=None,
        attachments=None,
        referenced=None,
    ):
        self.id = message_id
        self.author = author
        self.channel = channel
        self.content = content
        self.mentions = mentions or []
        self.attachments = attachments or []
        self.guild = None
        self.reference = (
            SimpleNamespace(
                resolved=referenced,
                message_id=getattr(referenced, "id", None),
            )
            if referenced is not None
            else None
        )
        self.replies = []

    async def reply(self, content, *, mention_author=False):
        self.replies.append((content, mention_author))


def test_reply_to_bot_continues_public_vision_context_without_remention():
    ai_service = FakeMentionAIService()
    bot = CSEHQBot(
        SimpleNamespace(
            ai_session_service=NoSessionService(),
            ai_service=ai_service,
        ),
        enable_message_content=True,
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    user = SimpleNamespace(
        bot=False,
        id=123,
        name="alice",
        display_name="Alice",
    )
    bot._connection.user = bot_user
    channel = FakeMentionChannel()

    original = FakeReplyMessage(
        message_id=1,
        author=user,
        channel=channel,
        content="<@999> phân tích ý tưởng trong ảnh này",
        mentions=[bot_user],
        attachments=[FakeImageAttachment()],
    )
    bot_response = FakeReplyMessage(
        message_id=2,
        author=bot_user,
        channel=channel,
        content="Ý tưởng này là hệ thống AI camera chấm công và giám sát.",
        referenced=original,
    )
    follow_up = FakeReplyMessage(
        message_id=3,
        author=user,
        channel=channel,
        content="pros and cons của cái này đi bro",
        referenced=bot_response,
    )

    asyncio.run(bot.on_message(follow_up))

    assert ai_service.kwargs["question"] == "pros and cons của cái này đi bro"
    assert ai_service.kwargs["history_messages"] == [
        {
            "role": "user",
            "content": "phân tích ý tưởng trong ảnh này",
        },
        {
            "role": "assistant",
            "content": "Ý tưởng này là hệ thống AI camera chấm công và giám sát.",
        },
    ]
    assert "images" not in ai_service.kwargs
    assert ai_service.kwargs["allow_actions"] is False
    assert follow_up.replies == [("Mention reply", False)]


def test_reply_chain_does_not_import_another_users_original_prompt():
    ai_service = FakeMentionAIService()
    bot = CSEHQBot(
        SimpleNamespace(
            ai_session_service=NoSessionService(),
            ai_service=ai_service,
        ),
        enable_message_content=True,
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    first_user = SimpleNamespace(
        bot=False,
        id=111,
        name="first",
        display_name="First",
    )
    second_user = SimpleNamespace(
        bot=False,
        id=222,
        name="second",
        display_name="Second",
    )
    bot._connection.user = bot_user
    channel = FakeMentionChannel()

    original = FakeReplyMessage(
        message_id=10,
        author=first_user,
        channel=channel,
        content="<@999> private-ish context from first user",
        mentions=[bot_user],
    )
    bot_response = FakeReplyMessage(
        message_id=11,
        author=bot_user,
        channel=channel,
        content="Public bot answer.",
        referenced=original,
    )
    follow_up = FakeReplyMessage(
        message_id=12,
        author=second_user,
        channel=channel,
        content="explain that answer",
        referenced=bot_response,
    )

    asyncio.run(bot.on_message(follow_up))

    assert ai_service.kwargs["history_messages"] == [
        {"role": "assistant", "content": "Public bot answer."}
    ]


def test_visual_reply_reuses_originating_image_when_followup_needs_it():
    ai_service = FakeMentionAIService()
    bot = CSEHQBot(
        SimpleNamespace(
            ai_session_service=NoSessionService(),
            ai_service=ai_service,
        ),
        enable_message_content=True,
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    user = SimpleNamespace(
        bot=False,
        id=123,
        name="alice",
        display_name="Alice",
    )
    bot._connection.user = bot_user
    channel = FakeMentionChannel()

    original = FakeReplyMessage(
        message_id=21,
        author=user,
        channel=channel,
        content="<@999> phân tích ảnh này",
        mentions=[bot_user],
        attachments=[FakeImageAttachment()],
    )
    bot_response = FakeReplyMessage(
        message_id=22,
        author=bot_user,
        channel=channel,
        content="Mình đã phân tích ảnh.",
        referenced=original,
    )
    follow_up = FakeReplyMessage(
        message_id=23,
        author=user,
        channel=channel,
        content="chữ ở góc trái trong ảnh là gì bro?",
        referenced=bot_response,
    )

    asyncio.run(bot.on_message(follow_up))

    assert len(ai_service.kwargs["images"]) == 1
    assert ai_service.kwargs["images"][0].filename == "idea.png"
