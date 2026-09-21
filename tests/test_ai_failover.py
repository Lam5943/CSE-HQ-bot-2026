import asyncio
from types import SimpleNamespace

import pytest

from cse_hq_bot.ai.base import AIMessage, AIProviderResponse, RetrievedContextRecord
from cse_hq_bot.ai.openai_provider import OpenAIProvider
from cse_hq_bot.ai.provider_router import AIProviderRouter
from cse_hq_bot.config import load_config
from cse_hq_bot.db import Database
from cse_hq_bot.errors import (
    AIConfigurationError,
    AIMalformedResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AISessionBusyError,
    AITimeoutError,
    PermissionDeniedError,
)
from cse_hq_bot.factory import build_ai_provider
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.services.ai_service import GroundedAnswer
from cse_hq_bot.services.ai_session_service import AISessionService

MESSAGES = [AIMessage(role="user", content="What is still open?")]
CONTEXT = [
    RetrievedContextRecord("task", "TASK-012", "Task", "Task context", None, "test"),
    RetrievedContextRecord(
        "decision", "DEC-004", "Decision", "Decision context", None, "test"
    ),
    RetrievedContextRecord(
        "github_pull_request", "GH-PR-9", "PR", "PR context", None, "test"
    ),
]


class _FakeResponses:
    def __init__(self, *, result=None, error=None, delay=0):
        self.result = result
        self.error = error
        self.delay = delay
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


class _FakeOpenAIClient:
    def __init__(self, responses):
        self.responses = responses
        self.options = []

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


class _FakeAsyncOpenAI:
    def __init__(self, responses, *, error=None):
        self.responses = responses
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _FakeOpenAIClient(self.responses)


class AuthenticationError(Exception):
    status_code = 401


class RateLimitError(Exception):
    status_code = 429


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class InternalServerError(Exception):
    status_code = 503


class BadRequestError(Exception):
    status_code = 400


def _openai_provider(monkeypatch, *, result=None, error=None, delay=0):
    from cse_hq_bot.ai import openai_provider as module

    responses = _FakeResponses(result=result, error=error, delay=delay)
    client_factory = _FakeAsyncOpenAI(responses)
    monkeypatch.setattr(module, "openai", SimpleNamespace())
    monkeypatch.setattr(module, "AsyncOpenAI", client_factory)
    provider = OpenAIProvider("secret", "configured-model")
    return provider, responses, client_factory


def test_openai_provider_success_uses_responses_api_and_shared_prompt(monkeypatch):
    provider, responses, factory = _openai_provider(
        monkeypatch,
        result=SimpleNamespace(output_text="  grounded answer  "),
    )

    response = asyncio.run(
        provider.generate(
            system_instruction="read only",
            messages=MESSAGES,
            context_records=CONTEXT,
            timeout_seconds=7,
        )
    )

    assert response == AIProviderResponse(
        text="grounded answer",
        provider="openai",
        model="configured-model",
    )
    assert factory.calls == [{"api_key": "secret", "max_retries": 0}]
    assert provider.client.options == [{"timeout": 7, "max_retries": 0}]
    assert responses.calls[0]["model"] == "configured-model"
    assert responses.calls[0]["store"] is False
    assert "<SYSTEM_INSTRUCTIONS>\nread only" in responses.calls[0]["input"]
    assert all(record.source_id in responses.calls[0]["input"] for record in CONTEXT)


def test_openai_provider_extracts_nested_output(monkeypatch):
    result = SimpleNamespace(
        output_text=None,
        output=[
            SimpleNamespace(
                content=[SimpleNamespace(text="first"), SimpleNamespace(text="second")]
            )
        ],
    )
    provider, _, _ = _openai_provider(monkeypatch, result=result)

    response = asyncio.run(
        provider.generate(
            system_instruction="sys",
            messages=MESSAGES,
            context_records=[],
            timeout_seconds=1,
        )
    )

    assert response.text == "first\nsecond"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("bad key"), AIConfigurationError),
        (RateLimitError("limited"), AIRateLimitError),
        (APITimeoutError("slow"), AITimeoutError),
        (APIConnectionError("offline"), AIProviderUnavailableError),
        (InternalServerError("overloaded"), AIProviderUnavailableError),
        (BadRequestError("bad input"), AIProviderError),
        (RuntimeError("unexpected"), AIProviderError),
    ],
)
def test_openai_provider_normalizes_sdk_failures(monkeypatch, error, expected):
    provider, _, _ = _openai_provider(monkeypatch, error=error)

    with pytest.raises(expected):
        asyncio.run(
            provider.generate(
                system_instruction="sys",
                messages=MESSAGES,
                context_records=CONTEXT,
                timeout_seconds=1,
            )
        )


@pytest.mark.parametrize(
    "result", [None, SimpleNamespace(output_text=""), SimpleNamespace(output=[])]
)
def test_openai_provider_rejects_empty_or_malformed_responses(monkeypatch, result):
    provider, _, _ = _openai_provider(monkeypatch, result=result)

    with pytest.raises(AIMalformedResponseError):
        asyncio.run(
            provider.generate(
                system_instruction="sys",
                messages=MESSAGES,
                context_records=[],
                timeout_seconds=1,
            )
        )


def test_openai_provider_enforces_async_timeout(monkeypatch):
    provider, _, _ = _openai_provider(
        monkeypatch,
        result=SimpleNamespace(output_text="late"),
        delay=0.05,
    )

    with pytest.raises(AITimeoutError):
        asyncio.run(
            provider.generate(
                system_instruction="sys",
                messages=MESSAGES,
                context_records=[],
                timeout_seconds=0.01,
            )
        )


def test_openai_provider_requires_key_model_and_sdk(monkeypatch):
    from cse_hq_bot.ai import openai_provider as module

    with pytest.raises(AIConfigurationError, match="OPENAI_API_KEY"):
        OpenAIProvider(None, "model")
    with pytest.raises(AIConfigurationError, match="OPENAI_MODEL"):
        OpenAIProvider("secret", None)
    monkeypatch.setattr(module, "openai", None)
    monkeypatch.setattr(module, "AsyncOpenAI", None)
    with pytest.raises(AIConfigurationError, match="package"):
        OpenAIProvider("secret", "model")


class _RecordingProvider:
    def __init__(self, *, response=None, error=None, wait_event=None):
        self.response = response or AIProviderResponse(text="ok")
        self.error = error
        self.wait_event = wait_event
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.wait_event is not None:
            await self.wait_event.wait()
        if self.error:
            raise self.error
        return self.response


def _run_router(router):
    return asyncio.run(
        router.generate(
            system_instruction="same system",
            messages=MESSAGES,
            context_records=CONTEXT,
            timeout_seconds=3,
        )
    )


def test_router_primary_success_does_not_call_fallback():
    primary = _RecordingProvider(
        response=AIProviderResponse(
            text="primary", provider="gemini", model="gemini-model"
        )
    )
    fallback = _RecordingProvider()
    router = AIProviderRouter(primary, fallback, fallback_enabled=True)

    response = _run_router(router)

    assert response.text == "primary"
    assert len(primary.calls) == 1
    assert fallback.calls == []
    assert router.metrics == {"primary_success": 1}
    assert router.health_snapshot()["primary_result"] == "success"


@pytest.mark.parametrize(
    "primary_error",
    [
        AIRateLimitError("limited"),
        AITimeoutError("slow"),
        AIProviderUnavailableError("down"),
    ],
)
def test_router_eligible_failure_uses_fallback_once_with_identical_grounding(
    primary_error,
):
    primary = _RecordingProvider(error=primary_error)
    fallback = _RecordingProvider(
        response=AIProviderResponse(
            text="fallback", provider="openai", model="backup-model"
        )
    )
    router = AIProviderRouter(primary, fallback, fallback_enabled=True)

    response = _run_router(router)

    assert response.fallback_used is True
    assert response.provider == "openai"
    assert len(primary.calls) == len(fallback.calls) == 1
    assert primary.calls[0] == fallback.calls[0]
    assert fallback.calls[0]["context_records"] == CONTEXT
    assert router.metrics["fallback_attempt"] == 1
    assert router.metrics["fallback_success"] == 1
    assert router.health_snapshot() == {
        "primary_result": "failed",
        "primary_error_category": primary_error.__class__.__name__,
        "fallback_result": "success",
        "fallback_error_category": None,
    }


@pytest.mark.parametrize(
    "primary_error",
    [
        AIConfigurationError("bad config"),
        AIMalformedResponseError("bad response"),
        AIProviderError("generic"),
        PermissionDeniedError("domain"),
    ],
)
def test_router_does_not_mask_noneligible_errors(primary_error):
    primary = _RecordingProvider(error=primary_error)
    fallback = _RecordingProvider()
    router = AIProviderRouter(primary, fallback, fallback_enabled=True)

    with pytest.raises(type(primary_error)):
        _run_router(router)
    assert fallback.calls == []


def test_router_disabled_propagates_eligible_primary_error():
    primary = _RecordingProvider(error=AITimeoutError("slow"))
    fallback = _RecordingProvider()
    router = AIProviderRouter(primary, fallback, fallback_enabled=False)

    with pytest.raises(AITimeoutError):
        _run_router(router)
    assert fallback.calls == []


def test_router_retries_rate_limit_once_before_success():
    primary = _RecordingProvider(error=AIRateLimitError("limited"))
    router = AIProviderRouter(
        primary,
        None,
        fallback_enabled=False,
        primary_retry_count=1,
        primary_retry_delay_seconds=0,
    )

    async def run():
        original_generate = primary.generate
        calls = 0

        async def generate(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AIRateLimitError("limited")
            return AIProviderResponse(text="recovered", provider="groq")

        primary.generate = generate
        try:
            return await router.generate(
                system_instruction="sys",
                messages=MESSAGES,
                context_records=CONTEXT,
                timeout_seconds=3,
            )
        finally:
            primary.generate = original_generate

    response = asyncio.run(run())
    assert response.text == "recovered"
    assert router.metrics["primary_retry"] == 1
    assert router.metrics["primary_success"] == 1


def test_router_does_not_retry_timeout():
    primary = _RecordingProvider(error=AITimeoutError("slow"))
    router = AIProviderRouter(
        primary,
        None,
        fallback_enabled=False,
        primary_retry_count=2,
        primary_retry_delay_seconds=0,
    )

    with pytest.raises(AITimeoutError):
        _run_router(router)
    assert len(primary.calls) == 1
    assert router.metrics["primary_retry"] == 0


def test_router_dual_failure_is_controlled_and_never_loops():
    primary = _RecordingProvider(error=AITimeoutError("slow"))
    fallback = _RecordingProvider(error=AIRateLimitError("limited"))
    router = AIProviderRouter(primary, fallback, fallback_enabled=True)

    with pytest.raises(AIProviderUnavailableError, match="All configured"):
        _run_router(router)
    assert len(primary.calls) == len(fallback.calls) == 1
    assert router.metrics["fallback_failure"] == 1
    assert router.health_snapshot()["fallback_result"] == "failed"


def test_router_missing_fallback_config_is_deferred_until_needed():
    primary = _RecordingProvider(
        response=AIProviderResponse(text="primary", provider="gemini")
    )
    router = AIProviderRouter(
        primary,
        None,
        fallback_enabled=True,
        fallback_configuration_error=AIConfigurationError("missing OpenAI config"),
    )
    assert _run_router(router).text == "primary"

    primary.error = AITimeoutError("slow")
    with pytest.raises(AIConfigurationError, match="missing OpenAI config"):
        _run_router(router)


def _config(**overrides):
    values = {
        "ai_provider": "fake",
        "gemini_api_key": None,
        "ai_model": "gemini-model",
        "groq_api_key": None,
        "groq_model": "qwen/qwen3.8-27b",
        "ai_primary_retries": 0,
        "ai_fallback_enabled": False,
        "ai_fallback_provider": "openai",
        "openai_api_key": None,
        "openai_model": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_factory_configuration_disabled_enabled_missing_and_invalid(monkeypatch):
    from cse_hq_bot import factory as module

    def unexpected_openai(*args, **kwargs):
        raise AssertionError("OpenAI must not initialize when fallback is disabled")

    monkeypatch.setattr(module, "OpenAIProvider", unexpected_openai)
    provider = build_ai_provider(_config())
    assert provider.__class__.__name__ == "FakeAIProvider"

    monkeypatch.setattr(module, "OpenAIProvider", OpenAIProvider)

    groq = build_ai_provider(
        _config(
            ai_provider="groq",
            groq_api_key="groq-placeholder",
            ai_primary_retries=0,
        )
    )
    assert groq.__class__.__name__ == "GroqProvider"

    router = build_ai_provider(_config(ai_fallback_enabled=True))
    assert isinstance(router, AIProviderRouter)
    assert router.fallback is None
    assert isinstance(router.fallback_configuration_error, AIConfigurationError)

    with pytest.raises(AIConfigurationError, match="Unsupported"):
        build_ai_provider(
            _config(ai_fallback_enabled=True, ai_fallback_provider="unsupported")
        )
    with pytest.raises(AIConfigurationError, match="primary provider"):
        build_ai_provider(_config(ai_provider="unsupported"))


def test_load_config_reads_fallback_settings(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-placeholder")
    monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    monkeypatch.setenv("AI_PRIMARY_RETRIES", "1")
    monkeypatch.setenv("AI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AI_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    monkeypatch.setenv("OPENAI_MODEL", "configured-openai-model")

    config = load_config()

    assert config.groq_api_key == "groq-placeholder"
    assert config.groq_model == "qwen/qwen3.8-27b"
    assert config.ai_primary_retries == 1
    assert config.ai_fallback_enabled is True
    assert config.ai_fallback_provider == "openai"
    assert config.openai_api_key == "placeholder"
    assert config.openai_model == "configured-openai-model"


class _RouterAIService:
    def __init__(self, router):
        self.router = router

    async def answer_question(self, *, actor, question, history_messages):
        messages = [
            AIMessage(role=message["role"], content=message["content"])
            for message in history_messages
        ]
        messages.append(AIMessage(role="user", content=question))
        response = await self.router.generate(
            system_instruction="same system",
            messages=messages,
            context_records=CONTEXT,
            timeout_seconds=2,
        )
        return GroundedAnswer(response.text, [], [], "test")


def _session_service(tmp_path, router):
    db = Database(str(tmp_path / "sessions.db"))
    db.initialize()
    return AISessionService(
        AISessionRepository(db),
        _RouterAIService(router),
        max_history_messages=6,
    )


def test_session_history_remains_continuous_across_provider_switches(tmp_path):
    primary = _RecordingProvider(error=AITimeoutError("slow"))
    fallback = _RecordingProvider(
        response=AIProviderResponse(text="fallback answer", provider="openai")
    )
    router = AIProviderRouter(primary, fallback, fallback_enabled=True)
    sessions = _session_service(tmp_path, router)
    actor = Actor("owner", Role.MEMBER)
    session = sessions.create_session(actor, "thread-1")

    first = asyncio.run(
        sessions.handle_message(
            actor=actor,
            session_id=session["id"],
            discord_thread_id="thread-1",
            content="first",
        )
    )
    primary.error = None
    primary.response = AIProviderResponse(text="primary answer", provider="gemini")
    second = asyncio.run(
        sessions.handle_message(
            actor=actor,
            session_id=session["id"],
            discord_thread_id="thread-1",
            content="second",
        )
    )

    assert first.content == "fallback answer"
    assert second.content == "primary answer"
    assert any(
        message.content == "fallback answer" for message in primary.calls[1]["messages"]
    )
    persisted = sessions.get_session_messages(actor, session["id"])
    assert [message["content"] for message in persisted] == [
        "first",
        "fallback answer",
        "second",
        "primary answer",
    ]


class _BlockingFallback(_RecordingProvider):
    def __init__(self):
        super().__init__(
            response=AIProviderResponse(text="fallback", provider="openai")
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return self.response


def test_busy_lock_covers_primary_failure_and_fallback_attempt(tmp_path):
    primary = _RecordingProvider(error=AIProviderUnavailableError("down"))
    fallback = _BlockingFallback()
    router = AIProviderRouter(primary, fallback, fallback_enabled=True)
    sessions = _session_service(tmp_path, router)
    actor = Actor("owner", Role.MEMBER)
    session = sessions.create_session(actor, "thread-1")

    async def runner():
        first = asyncio.create_task(
            sessions.handle_message(
                actor=actor,
                session_id=session["id"],
                discord_thread_id="thread-1",
                content="first",
            )
        )
        await fallback.started.wait()
        with pytest.raises(AISessionBusyError):
            await sessions.handle_message(
                actor=actor,
                session_id=session["id"],
                discord_thread_id="thread-1",
                content="second",
            )
        fallback.release.set()
        await first

    asyncio.run(runner())
    assert len(primary.calls) == len(fallback.calls) == 1


def test_router_uses_rate_limit_retry_after_hint(monkeypatch):
    primary = _RecordingProvider()
    router = AIProviderRouter(
        primary,
        None,
        fallback_enabled=False,
        primary_retry_count=1,
        primary_retry_delay_seconds=1,
    )
    calls = 0
    delays = []

    async def generate(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AIRateLimitError("limited", retry_after_seconds=2.5)
        return AIProviderResponse(text="recovered", provider="groq")

    async def fake_sleep(delay):
        delays.append(delay)

    primary.generate = generate
    from cse_hq_bot.ai import provider_router as module
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

    response = _run_router(router)

    assert response.text == "recovered"
    assert delays == [2.5]
