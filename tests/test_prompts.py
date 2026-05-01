"""Tests for PromptRegistry: list and get prompts."""

import pytest
from app.config import Config
from app.mcp.prompts import PromptRegistry

pytestmark = pytest.mark.asyncio


def _make_config() -> Config:
    return Config(
        db_url="sqlite:///:memory:",
        mode="read-only",
        limit_default=100,
        timeout_ms=5000,
        enable_ui=True,
        enable_explanations=True,
        allowed_origins=[],
        allow_destructive=False,
        llm_provider="openai",
        llm_api_key="",
        llm_model="",
        llm_base_url=None,
        llm_timeout_ms=60000,
        chat_history_enabled=True,
        chat_history_limit=10,
    )


@pytest.fixture
def registry() -> PromptRegistry:
    return PromptRegistry(_make_config())


# ── list_prompts ───────────────────────────────────────────────────────────────


def test_list_prompts_returns_three(registry: PromptRegistry) -> None:
    result = registry.list_prompts()
    assert "prompts" in result
    assert len(result["prompts"]) == 3


def test_list_prompts_names(registry: PromptRegistry) -> None:
    names = {p["name"] for p in registry.list_prompts()["prompts"]}
    assert names == {"sql.assistant.role", "sql.query.plan", "db.design.schema"}


def test_list_prompts_have_description(registry: PromptRegistry) -> None:
    for p in registry.list_prompts()["prompts"]:
        assert p.get("description"), f"Prompt {p['name']} missing description"


# ── get_prompt ─────────────────────────────────────────────────────────────────


def test_get_assistant_role(registry: PromptRegistry) -> None:
    result = registry.get_prompt("sql.assistant.role", {})
    assert result is not None
    messages = result["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "sql.schema" in messages[0]["content"]["text"]


def test_get_query_plan_includes_question(registry: PromptRegistry) -> None:
    result = registry.get_prompt("sql.query.plan", {"question": "find top users"})
    assert result is not None
    text = result["messages"][0]["content"]["text"]
    assert "find top users" in text


def test_get_query_plan_empty_question(registry: PromptRegistry) -> None:
    result = registry.get_prompt("sql.query.plan", {})
    assert result is not None
    # Must not crash on missing argument
    assert "messages" in result


def test_get_design_schema_includes_domain_and_db(registry: PromptRegistry) -> None:
    result = registry.get_prompt("db.design.schema", {"domain": "e-commerce", "db": "mysql"})
    assert result is not None
    text = result["messages"][0]["content"]["text"]
    assert "e-commerce" in text
    assert "mysql" in text


def test_get_design_schema_default_db(registry: PromptRegistry) -> None:
    result = registry.get_prompt("db.design.schema", {"domain": "blog"})
    assert result is not None
    text = result["messages"][0]["content"]["text"]
    assert "postgres" in text  # default db


def test_get_unknown_prompt_returns_none(registry: PromptRegistry) -> None:
    result = registry.get_prompt("nonexistent.prompt", {})
    assert result is None
