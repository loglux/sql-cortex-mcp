"""Tests for LLM provider selection and retry logic."""

from unittest.mock import patch

import httpx
import pytest
from app.assistant.service import _build_provider
from app.config import Config
from app.llm.base import LLM_MAX_RETRIES, LLMProvider
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.chat_completions import ChatCompletionsProvider
from app.llm.providers.ollama import OllamaProvider

pytestmark = pytest.mark.asyncio


def _make_config(**overrides) -> Config:
    defaults = dict(
        db_url="sqlite:///:memory:",
        mode="read-only",
        limit_default=100,
        timeout_ms=5000,
        enable_ui=True,
        enable_explanations=True,
        allowed_origins=["http://localhost:8000"],
        allow_destructive=False,
        llm_provider="openai",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url=None,
        llm_timeout_ms=60000,
        chat_history_enabled=True,
        chat_history_limit=10,
    )
    defaults.update(overrides)
    return Config(**defaults)


# ── Provider selection ─────────────────────────────────────────────────────


def test_build_provider_openai() -> None:
    cfg = _make_config(llm_provider="openai")
    provider = _build_provider(cfg)
    assert isinstance(provider, ChatCompletionsProvider)
    assert provider.base_url == "https://api.openai.com"


def test_build_provider_anthropic() -> None:
    cfg = _make_config(llm_provider="anthropic")
    provider = _build_provider(cfg)
    assert isinstance(provider, AnthropicProvider)


def test_build_provider_ollama() -> None:
    cfg = _make_config(llm_provider="ollama")
    provider = _build_provider(cfg)
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://localhost:11434"


def test_build_provider_deepseek() -> None:
    cfg = _make_config(llm_provider="deepseek")
    provider = _build_provider(cfg)
    assert isinstance(provider, ChatCompletionsProvider)
    assert provider.base_url == "https://api.deepseek.com"


def test_build_provider_gemini() -> None:
    cfg = _make_config(llm_provider="gemini")
    provider = _build_provider(cfg)
    assert isinstance(provider, ChatCompletionsProvider)
    assert "generativelanguage" in provider.base_url


def test_build_provider_custom_base_url() -> None:
    cfg = _make_config(llm_provider="openai", llm_base_url="https://my-proxy.example.com")
    provider = _build_provider(cfg)
    assert isinstance(provider, ChatCompletionsProvider)
    assert provider.base_url == "https://my-proxy.example.com"


def test_build_provider_timeout_passed() -> None:
    cfg = _make_config(llm_timeout_ms=30000)
    provider = _build_provider(cfg)
    assert provider.timeout == 30.0


# ── Retry logic ────────────────────────────────────────────────────────────


class _FlakyProvider(LLMProvider):
    """Provider that fails N times then succeeds."""

    def __init__(self, fail_times: int, exc: Exception) -> None:
        self.fail_times = fail_times
        self.exc = exc
        self.attempts = 0

    async def _generate(self, messages):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise self.exc
        return {"text": "ok", "raw": {}}


def _make_http_error(status: int) -> httpx.HTTPStatusError:
    response = httpx.Response(status_code=status)
    return httpx.HTTPStatusError(
        message=f"{status}", request=httpx.Request("POST", "http://x"), response=response
    )


async def test_retry_on_500() -> None:
    provider = _FlakyProvider(fail_times=1, exc=_make_http_error(500))
    with patch("app.llm.base.LLM_RETRY_BASE_DELAY", 0):
        result = await provider.generate([])
    assert result["text"] == "ok"
    assert provider.attempts == 2


async def test_retry_on_429() -> None:
    provider = _FlakyProvider(fail_times=1, exc=_make_http_error(429))
    with patch("app.llm.base.LLM_RETRY_BASE_DELAY", 0):
        result = await provider.generate([])
    assert result["text"] == "ok"
    assert provider.attempts == 2


async def test_no_retry_on_400() -> None:
    provider = _FlakyProvider(fail_times=1, exc=_make_http_error(400))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.generate([])
    assert provider.attempts == 1  # no retry for client errors


async def test_retry_on_timeout() -> None:
    provider = _FlakyProvider(fail_times=1, exc=httpx.TimeoutException("timeout"))
    with patch("app.llm.base.LLM_RETRY_BASE_DELAY", 0):
        result = await provider.generate([])
    assert result["text"] == "ok"
    assert provider.attempts == 2


async def test_retry_on_connect_error() -> None:
    provider = _FlakyProvider(fail_times=1, exc=httpx.ConnectError("refused"))
    with patch("app.llm.base.LLM_RETRY_BASE_DELAY", 0):
        result = await provider.generate([])
    assert result["text"] == "ok"


async def test_retry_exhausted_raises() -> None:
    provider = _FlakyProvider(fail_times=10, exc=_make_http_error(503))
    with patch("app.llm.base.LLM_RETRY_BASE_DELAY", 0):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate([])
    assert provider.attempts == 1 + LLM_MAX_RETRIES
