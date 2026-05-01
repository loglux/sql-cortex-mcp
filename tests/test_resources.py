"""Tests for ResourceRegistry: list and read resources."""

import json

import pytest
from app.config import Config
from app.mcp.resources import ResourceRegistry

pytestmark = pytest.mark.asyncio


def _make_config(**overrides) -> Config:
    defaults = dict(
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
    defaults.update(overrides)
    return Config(**defaults)


@pytest.fixture
def registry() -> ResourceRegistry:
    return ResourceRegistry(_make_config())


# ── list_resources ─────────────────────────────────────────────────────────────


def test_list_resources_returns_two(registry: ResourceRegistry) -> None:
    result = registry.list_resources()
    assert "resources" in result
    assert len(result["resources"]) == 2


def test_list_resources_uris(registry: ResourceRegistry) -> None:
    uris = {r["uri"] for r in registry.list_resources()["resources"]}
    assert "resource://schema" in uris
    assert "resource://config" in uris


def test_list_resources_have_mime_type(registry: ResourceRegistry) -> None:
    for r in registry.list_resources()["resources"]:
        assert r.get("mimeType") == "application/json"


# ── read_resource ──────────────────────────────────────────────────────────────


def test_read_schema_resource(registry: ResourceRegistry) -> None:
    result = registry.read_resource("resource://schema")
    assert result is not None
    contents = result["contents"]
    assert len(contents) == 1
    assert contents[0]["uri"] == "resource://schema"
    assert contents[0]["mimeType"] == "application/json"
    # Must be valid JSON
    parsed = json.loads(contents[0]["text"])
    assert isinstance(parsed, dict)


def test_read_config_resource(registry: ResourceRegistry) -> None:
    result = registry.read_resource("resource://config")
    assert result is not None
    data = json.loads(result["contents"][0]["text"])
    assert data["mode"] == "read-only"
    assert data["limit_default"] == 100
    assert "db_type" in data
    assert "allow_destructive" in data


def test_read_config_resource_no_secrets(registry: ResourceRegistry) -> None:
    result = registry.read_resource("resource://config")
    text = result["contents"][0]["text"]
    # API keys must not appear
    assert "api_key" not in text
    assert "llm_api_key" not in text


def test_read_unknown_resource_returns_none(registry: ResourceRegistry) -> None:
    result = registry.read_resource("resource://nonexistent")
    assert result is None


def test_read_config_reflects_mode(registry: ResourceRegistry) -> None:
    reg = ResourceRegistry(_make_config(mode="execute"))
    data = json.loads(reg.read_resource("resource://config")["contents"][0]["text"])
    assert data["mode"] == "execute"
