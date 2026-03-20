"""Regression tests for MCP HTTP endpoints."""

import app.main as main_module
import pytest
import pytest_asyncio
from app.config import Config
from app.main import app
from app.mcp.registry import ToolRegistry
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

HEADERS = {
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-11-25",
}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_initialize(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["protocolVersion"] == "2025-11-25"
    assert body["result"]["serverInfo"]["name"] == "sql-cortex-mcp"


async def test_tools_list(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        },
    )
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "sql.query" in names
    assert "sql.schema" in names
    assert "sql.explain" in names
    assert "db.design" in names
    assert "db.schema.diff" in names
    assert "db.migrate.plan" in names
    assert "db.apply" in names
    assert "db.migrate" in names
    assert "db.migrate.plan_apply" in names


async def test_tools_list_count(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        },
    )
    tools = resp.json()["result"]["tools"]
    assert len(tools) == 9


async def test_sql_query_select_one(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "sql.query", "arguments": {"sql": "SELECT 1 AS n"}},
        },
    )
    assert resp.status_code == 200
    content = resp.json()["result"]["structuredContent"]
    assert content["row_count"] == 1
    assert content["rows"][0]["n"] == 1


async def test_sql_query_blocked_in_read_only(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "sql.query", "arguments": {"sql": "DROP TABLE users"}},
        },
    )
    assert resp.status_code == 200
    content = resp.json()["result"]["structuredContent"]
    assert "error" in content


async def test_sql_schema_returns_dict(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "sql.schema", "arguments": {}},
        },
    )
    assert resp.status_code == 200
    content = resp.json()["result"]["structuredContent"]
    assert "schema" in content


async def test_unknown_tool_returns_error(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "nonexistent.tool", "arguments": {}},
        },
    )
    assert resp.status_code == 200
    content = resp.json()["result"]["structuredContent"]
    assert "error" in content


async def test_tools_list_has_annotations(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/list",
        },
    )
    tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
    assert tools["sql.query"]["annotations"]["readOnlyHint"] is True
    assert tools["sql.query"]["annotations"]["openWorldHint"] is False
    assert tools["db.apply"]["annotations"]["destructiveHint"] is True
    assert tools["db.apply"]["annotations"]["openWorldHint"] is False


async def test_unknown_method_returns_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "unknown/method",
        },
    )
    assert resp.status_code == 404


async def test_invalid_mcp_version_returns_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers={
            **HEADERS,
            "MCP-Protocol-Version": "1999-01-01",
        },
        json={"jsonrpc": "2.0", "id": 8, "method": "initialize", "params": {}},
    )
    assert resp.status_code == 400


async def test_resources_list(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "resources/list",
        },
    )
    assert resp.status_code == 200
    assert "resources" in resp.json()["result"]


async def test_prompts_list(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "prompts/list",
        },
    )
    assert resp.status_code == 200
    assert "prompts" in resp.json()["result"]


async def test_settings_models_endpoint_uses_runtime_provider_state(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch(provider: str, api_key: str, base_url: str):
        assert provider == "openai"
        return ["gpt-5.4-mini", "gpt-5.4"]

    monkeypatch.setattr("app.web.routes._fetch_provider_models", fake_fetch)
    resp = await client.get("/settings/models", params={"provider": "openai"})
    assert resp.status_code == 200
    assert "gpt-5.4-mini" in resp.text


async def test_ui_routes_use_latest_runtime_config(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_config = Config(
        db_url="sqlite:///./runtime-refresh.db",
        mode="execute",
        limit_default=25,
        timeout_ms=3210,
        enable_ui=True,
        enable_explanations=True,
        allowed_origins=["http://test"],
        allow_destructive=True,
        llm_provider="openai",
        llm_api_key="",
        llm_model="gpt-5.4-mini",
        llm_base_url=None,
        openai_api_mode="chat",
        chat_history_enabled=True,
        chat_history_limit=10,
    )
    monkeypatch.setattr(main_module, "config", new_config)
    monkeypatch.setattr(main_module, "registry", ToolRegistry())
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "sqlite:///./runtime-refresh.db" in resp.text
    assert "execute" in resp.text
