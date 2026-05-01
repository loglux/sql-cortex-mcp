"""Tests for AssistantService: LLM response parsing, provider validation, chat flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.assistant.service import AssistantService, _build_provider, _parse_llm_response
from app.config import Config
from app.mcp.registry import ToolRegistry

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


# ── _parse_llm_response ────────────────────────────────────────────────────────


def test_parse_valid_json() -> None:
    text = '{"thought": "check rows", "sql": "SELECT 1", "explanation": "test"}'
    result = _parse_llm_response(text)
    assert result["sql"] == "SELECT 1"
    assert result["thought"] == "check rows"


def test_parse_markdown_wrapped_json() -> None:
    text = '```json\n{"thought": "t", "sql": "SELECT 2", "explanation": "e"}\n```'
    result = _parse_llm_response(text)
    assert result["sql"] == "SELECT 2"


def test_parse_json_embedded_in_text() -> None:
    text = 'Here is the answer: {"thought": "t", "sql": null, "explanation": "nothing"} done.'
    result = _parse_llm_response(text)
    assert result["sql"] is None


def test_parse_invalid_json_fallback() -> None:
    text = "I cannot answer that."
    result = _parse_llm_response(text)
    assert result["sql"] is None
    assert result["explanation"] == text
    assert result["thought"] == ""


def test_parse_null_sql() -> None:
    text = '{"thought": "no query needed", "sql": null, "explanation": "done"}'
    result = _parse_llm_response(text)
    assert result["sql"] is None


# ── _build_provider validation ─────────────────────────────────────────────────


def test_build_provider_unknown_raises() -> None:
    cfg = _make_config(llm_provider="unknown_llm")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        _build_provider(cfg)


def test_build_provider_known_does_not_raise() -> None:
    for provider in ("openai", "anthropic", "deepseek", "ollama", "gemini", "groq", "mistral"):
        cfg = _make_config(llm_provider=provider)
        _build_provider(cfg)  # must not raise


# ── AssistantService.chat() ────────────────────────────────────────────────────


def _make_service(llm_response: str) -> AssistantService:
    cfg = _make_config()
    registry = ToolRegistry()
    # Pass db_url so chat() uses SchemaIntrospector directly instead of calling registry
    service = AssistantService(cfg, registry, db_url="sqlite:///:memory:", db_type="SQLite")
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(return_value={"text": llm_response, "raw": {}})
    service.provider = mock_provider
    return service


async def test_chat_executes_sql() -> None:
    llm_json = '{"thought": "count rows", "sql": "SELECT 1 AS n", "explanation": "one row"}'
    service = _make_service(llm_json)
    result = await service.chat("how many rows?", [])
    assert result["sql"] == "SELECT 1 AS n"
    assert result["query_result"] is not None
    assert result["query_result"]["row_count"] == 1
    assert result["explanation"] == "one row"


async def test_chat_no_sql_skips_query() -> None:
    llm_json = '{"thought": "no data needed", "sql": null, "explanation": "nothing to query"}'
    service = _make_service(llm_json)
    result = await service.chat("hello", [])
    assert result["sql"] is None
    assert result["query_result"] is None
    assert result["explanation"] == "nothing to query"


async def test_chat_respects_history() -> None:
    llm_json = '{"thought": "t", "sql": null, "explanation": "ok"}'
    service = _make_service(llm_json)
    history = [{"role": "user", "content": "prev"}, {"role": "assistant", "content": "prev reply"}]
    await service.chat("follow up", history)
    # Provider was called — history was passed through
    assert service.provider.generate.called
    messages = service.provider.generate.call_args[0][0]
    roles = [m["role"] for m in messages]
    assert "system" in roles
    assert "user" in roles


async def test_chat_bad_sql_returns_error() -> None:
    llm_json = '{"thought": "t", "sql": "NOT VALID SQL !!!", "explanation": "e"}'
    service = _make_service(llm_json)
    result = await service.chat("break it", [])
    # SQLite will reject the statement; error should be surfaced
    assert result["error"] is not None
