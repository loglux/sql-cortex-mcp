"""
Per-session database switching.

Each MCP session can override which database it operates on via `db.use`.
Engines are cached by URL to avoid re-creating SQLAlchemy connections.
"""

import logging
from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app import settings_db

log = logging.getLogger(__name__)

_VALID_MODES = frozenset({"read-only", "execute"})


class SessionDBManager:
    """Manages per-session DB overrides and caches SQLAlchemy engines."""

    def __init__(self, default_url: str) -> None:
        self._default_url = default_url
        self._engines: Dict[str, Engine] = {}
        self._session_overrides: Dict[str, int] = {}  # session_id → connection_id

    def _get_engine(self, db_url: str) -> Engine:
        if db_url not in self._engines:
            self._engines[db_url] = create_engine(db_url)
        return self._engines[db_url]

    def set_session_db(self, session_id: str, connection_id: int) -> None:
        """Override which DB a session uses."""
        self._session_overrides[session_id] = connection_id

    def clear_session(self, session_id: str) -> None:
        """Remove session override (e.g. on session expiry)."""
        self._session_overrides.pop(session_id, None)

    def get_session_connection(self, session_id: str | None) -> dict | None:
        """Return the DB connection dict for a session, or None for default."""
        if not session_id:
            return None
        conn_id = self._session_overrides.get(session_id)
        if conn_id is None:
            return None
        return settings_db.get_db_connection(conn_id)

    def get_db_url(self, session_id: str | None) -> str:
        """Return the effective DB URL for a session."""
        conn = self.get_session_connection(session_id)
        if conn and conn.get("url"):
            return conn["url"]
        return self._default_url

    def get_engine_for_session(self, session_id: str | None) -> Engine:
        """Return the effective SQLAlchemy engine for a session."""
        url = self.get_db_url(session_id)
        return self._get_engine(url)

    def get_db_version(self, session_id: str | None) -> str:
        """Return the DB version string for a session's active connection."""
        from app.sql.executor import SQLExecutor

        url = self.get_db_url(session_id)
        try:
            return SQLExecutor(url).get_version()
        except Exception as e:
            log.warning("Failed to get DB version for %s: %s", url, e)
            return ""

    def get_db_type(self, session_id: str | None) -> str:
        """Return the DB type name for a session's active connection."""
        url = self.get_db_url(session_id).lower()
        if "postgresql" in url or "postgres" in url:
            return "PostgreSQL"
        if "mysql" in url:
            return "MySQL"
        if "sqlite" in url:
            return "SQLite"
        return "SQL"

    def get_mode(self, session_id: str | None, default_mode: str) -> str:
        """Return the effective mode for a session."""
        conn = self.get_session_connection(session_id)
        if conn and conn.get("mode"):
            mode = conn["mode"]
            if mode not in _VALID_MODES:
                log.warning(
                    "Invalid mode %r in connection %s, falling back to default",
                    mode,
                    conn.get("id"),
                )
                return default_mode
            return mode
        return default_mode

    def list_connections(self) -> list[dict[str, Any]]:
        """Return all registered connections with metadata (no secrets)."""
        from app.sql.executor import SQLExecutor

        connections = settings_db.get_all_db_connections()
        result = []
        for c in connections:
            url = c.get("url", "")
            version = ""
            if url:
                try:
                    ex = SQLExecutor(url)
                    version = ex.get_version()
                except Exception as e:
                    log.debug("Failed to get version for connection %s: %s", c.get("id"), e)
            result.append(
                {
                    "id": c["id"],
                    "name": c.get("name", ""),
                    "db_type": c.get("db_type", ""),
                    "version": version,
                    "mode": c.get("mode", "read-only"),
                    "is_active": bool(c.get("is_active")),
                    "host": _extract_host(url),
                }
            )
        return result


def _extract_host(url: str) -> str:
    """Extract host:port from a DB URL, masking credentials."""
    from urllib.parse import urlparse

    try:
        if ":///" in url:
            return url.split("///")[-1]
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        db_name = parsed.path.lstrip("/") if parsed.path else ""
        if host:
            return f"{db_name}@{host}{port}" if db_name else f"{host}{port}"
        return ""
    except Exception as e:
        log.debug("Failed to extract host from DB URL: %s", e)
        return ""
