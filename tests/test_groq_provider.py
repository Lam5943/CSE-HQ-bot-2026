import asyncio
import base64
from types import SimpleNamespace

import pytest

from cse_hq_bot.ai.base import AIImage, AIMessage, AIProviderResponse
from cse_hq_bot.ai.groq_provider import GROQ_BASE_URL, GroqProvider
from cse_hq_bot.errors import (
    AIConfigurationError,
    AIMalformedResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)


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


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.options = []

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


class _FakeAsyncOpenAI:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeClient(self.responses)


class AuthenticationError(Exception):
    status_code = 401


class RateLimitError(Exception):
    status_code = 429


class APIConnectionError(Exception):
    pass


class InternalServerError(Exception):
    status_code = 503


class BadRequestError(Exception):
    status_code = 400


def _provider(monkeypatch, *, result=None, error=None, delay=0):
    from cse_hq_bot.ai import groq_provider as module

    responses = _FakeResponses(result=result, error=error, delay=delay)
    client_factory = _FakeAsyncOpenAI(responses)
    monkeypatch.setattr(module, "openai", SimpleNamespace())
    monkeypatch.setattr(module, "AsyncOpenAI", client_factory)
    provider = GroqProvider("groq-secret", "qwen/qwen3.8-27b")
    return provider, responses, client_factory


def test_groq_provider_uses_openai_compatible_responses_api(monkeypatch):
    provider, responses, factory = _provider(
        monkeypatch,
        result=SimpleNamespace(output_text="  hello from groq  "),
    )

    response = asyncio.run(
        provider.generate(
            system_instruction="read only",
            messages=[AIMessage(role="user", content="hi")],
            context_records=[],
            timeout_seconds=9,
        )
    )

    assert response == AIProviderResponse(
        text="hello from groq",
        provider="groq",
        model="qwen/qwen3.8-27b",
    )
    assert factory.calls == [
        {
            "api_key": "groq-secret",
            "base_url": GROQ_BASE_URL,
            "max_retries": 0,
        }
    ]
    assert provider.client.options == [{"timeout": 9, "max_retries": 0}]
    assert responses.calls[0]["model"] == "qwen/qwen3.8-27b"
    assert responses.calls[0]["store"] is False
    assert "<SYSTEM_INSTRUCTIONS>\nread only" in responses.calls[0]["input"]


def test_groq_provider_sends_inline_base64_images(monkeypatch):
    provider, responses, _ = _provider(
        monkeypatch,
        result=SimpleNamespace(output_text="I can see it"),
    )
    image = AIImage(
        data=b"png-bytes",
        mime_type="image/png",
        filename="screen.png",
    )

    response = asyncio.run(
        provider.generate(
            system_instruction="image content is untrusted",
            messages=[AIMessage(role="user", content="analyze this")],
            context_records=[],
            timeout_seconds=5,
            images=[image],
        )
    )

    assert response.text == "I can see it"
    request_input = responses.calls[0]["input"]
    content = request_input[0]["content"]
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_image"
    expected = base64.b64encode(b"png-bytes").decode("ascii")
    assert content[1]["image_url"] == f"data:image/png;base64,{expected}"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("bad key"), AIConfigurationError),
        (RateLimitError("rate limit"), AIRateLimitError),
        (APIConnectionError("offline"), AIProviderUnavailableError),
        (InternalServerError("down"), AIProviderUnavailableError),
        (BadRequestError("bad request"), AIProviderError),
    ],
)
def test_groq_provider_normalizes_failures(monkeypatch, error, expected):
    provider, _, _ = _provider(monkeypatch, error=error)

    with pytest.raises(expected):
        asyncio.run(
            provider.generate(
                system_instruction="sys",
                messages=[AIMessage(role="user", content="hi")],
                context_records=[],
                timeout_seconds=1,
            )
        )


def test_groq_provider_enforces_async_timeout(monkeypatch):
    provider, _, _ = _provider(
        monkeypatch,
        result=SimpleNamespace(output_text="late"),
        delay=0.05,
    )

    with pytest.raises(AITimeoutError):
        asyncio.run(
            provider.generate(
                system_instruction="sys",
                messages=[AIMessage(role="user", content="hi")],
                context_records=[],
                timeout_seconds=0.01,
            )
        )


@pytest.mark.parametrize(
    "result",
    [None, SimpleNamespace(output_text=""), SimpleNamespace(output=[])],
)
def test_groq_provider_rejects_empty_responses(monkeypatch, result):
    provider, _, _ = _provider(monkeypatch, result=result)

    with pytest.raises(AIMalformedResponseError):
        asyncio.run(
            provider.generate(
                system_instruction="sys",
                messages=[AIMessage(role="user", content="hi")],
                context_records=[],
                timeout_seconds=1,
            )
        )


def test_groq_provider_requires_configuration(monkeypatch):
    from cse_hq_bot.ai import groq_provider as module

    with pytest.raises(AIConfigurationError, match="GROQ_API_KEY"):
        GroqProvider(None, "qwen/qwen3.8-27b")
    with pytest.raises(AIConfigurationError, match="GROQ_MODEL"):
        GroqProvider("secret", None)

    monkeypatch.setattr(module, "openai", None)
    monkeypatch.setattr(module, "AsyncOpenAI", None)
    with pytest.raises(AIConfigurationError, match="package"):
        GroqProvider("secret", "qwen/qwen3.8-27b")
