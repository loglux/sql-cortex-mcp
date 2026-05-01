"""Integration tests for /settings/* web routes."""

import app.settings_db as sdb
import pytest
import pytest_asyncio
from app.main import app
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def isolated_settings_db(tmp_path, monkeypatch):
    """Redirect settings DB to a temp directory so tests don't touch ./data/."""
    monkeypatch.setattr(sdb, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sdb, "DB_PATH", tmp_path / "settings.db")
    monkeypatch.setattr(sdb, "SECRET_PATH", tmp_path / ".secret")
    sdb.init_db()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Settings page ──────────────────────────────────────────────────────────────


async def test_settings_page_loads(client: AsyncClient) -> None:
    resp = await client.get("/settings")
    assert resp.status_code == 200
    assert "settings" in resp.text.lower()


# ── LLM settings ──────────────────────────────────────────────────────────────


async def test_save_llm_redirects(client: AsyncClient) -> None:
    resp = await client.post(
        "/settings/llm/openai",
        data={"api_key": "sk-test", "model": "gpt-4", "base_url": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/settings" in resp.headers["location"]


async def test_save_llm_persists(client: AsyncClient) -> None:
    await client.post(
        "/settings/llm/anthropic",
        data={"api_key": "anthro-key", "model": "claude-3", "base_url": ""},
        follow_redirects=False,
    )
    provider = sdb.get_llm_provider("anthropic")
    assert provider["model"] == "claude-3"


async def test_activate_llm_redirects(client: AsyncClient) -> None:
    resp = await client.post(
        "/settings/llm/openai/activate",
        follow_redirects=False,
    )
    assert resp.status_code == 303


async def test_activate_llm_sets_active(client: AsyncClient) -> None:
    # Provider must exist in DB before activating
    await client.post(
        "/settings/llm/anthropic",
        data={"api_key": "", "model": "claude-3", "base_url": ""},
        follow_redirects=False,
    )
    await client.post("/settings/llm/anthropic/activate", follow_redirects=False)
    active = sdb.get_active_llm_provider()
    assert active is not None
    assert active["provider"] == "anthropic"


# ── DB settings ───────────────────────────────────────────────────────────────


async def test_save_db_settings_redirects(client: AsyncClient) -> None:
    resp = await client.post(
        "/settings/db",
        data={
            "db_url": "sqlite:///:memory:",
            "db_mode": "read-only",
            "timeout_ms": "5000",
            "limit_default": "50",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/settings" in resp.headers["location"]


async def test_save_db_settings_persists(client: AsyncClient) -> None:
    await client.post(
        "/settings/db",
        data={
            "db_url": "sqlite:///:memory:",
            "db_mode": "execute",
            "timeout_ms": "9000",
            "limit_default": "200",
        },
        follow_redirects=False,
    )
    assert sdb.get_app_setting("timeout_ms") == "9000"
    assert sdb.get_app_setting("limit_default") == "200"


async def test_add_db_connection_redirects(client: AsyncClient) -> None:
    resp = await client.post(
        "/settings/db/add",
        data={"db_name": "test-db", "db_url": "sqlite:///:memory:", "db_mode": "read-only"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


async def test_add_db_connection_persists(client: AsyncClient) -> None:
    await client.post(
        "/settings/db/add",
        data={"db_name": "my-db", "db_url": "sqlite:///:memory:", "db_mode": "read-only"},
        follow_redirects=False,
    )
    connections = sdb.get_all_db_connections()
    names = [c["name"] for c in connections]
    assert "my-db" in names


async def test_activate_db_connection(client: AsyncClient) -> None:
    conn_id = sdb.save_db_connection(name="target", url="sqlite:///:memory:", mode="read-only")
    resp = await client.post(f"/settings/db/{conn_id}/activate", follow_redirects=False)
    assert resp.status_code == 303
    active = sdb.get_active_db_connection()
    assert active is not None
    assert active["id"] == conn_id


async def test_delete_db_connection(client: AsyncClient) -> None:
    conn_id = sdb.save_db_connection(name="to-delete", url="sqlite:///:memory:", mode="read-only")
    resp = await client.post(f"/settings/db/{conn_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert sdb.get_db_connection(conn_id) is None


# ── Chat settings ──────────────────────────────────────────────────────────────


async def test_save_chat_settings_redirects(client: AsyncClient) -> None:
    resp = await client.post(
        "/settings/chat",
        data={"chat_history_limit": "20"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


async def test_save_chat_settings_persists(client: AsyncClient) -> None:
    await client.post(
        "/settings/chat",
        data={"chat_history_enabled": "on", "chat_history_limit": "15"},
        follow_redirects=False,
    )
    assert sdb.get_app_setting("chat_history_limit") == "15"
    assert sdb.get_app_setting("chat_history_enabled") == "true"


# ── Reset ──────────────────────────────────────────────────────────────────────


async def test_reset_redirects(client: AsyncClient) -> None:
    resp = await client.post("/settings/reset", follow_redirects=False)
    assert resp.status_code == 303
    assert "reset=1" in resp.headers["location"]


async def test_reset_clears_data(client: AsyncClient) -> None:
    sdb.save_llm_provider("openai", api_key="key", model="gpt-4")
    await client.post("/settings/reset", follow_redirects=False)
    providers = sdb.get_all_llm_providers()
    # After reset all api_keys should be empty
    assert all(p["api_key"] == "" for p in providers)
