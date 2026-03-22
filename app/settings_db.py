"""
Settings store backed by ./data/settings.db (SQLite).
API keys are encrypted at rest with Fernet symmetric encryption.
The encryption key lives in ./data/.secret (auto-generated on first run)
or comes from the SECRET_KEY environment variable.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "settings.db"
SECRET_PATH = DATA_DIR / ".secret"

# ── Encryption ────────────────────────────────────────────────────────────────

try:
    from cryptography.fernet import Fernet, InvalidToken

    def _get_fernet() -> "Fernet | None":
        secret_key = os.getenv("SECRET_KEY", "").encode()
        if secret_key:
            # Env var must be a valid Fernet key (32 url-safe base64 bytes)
            try:
                return Fernet(secret_key)
            except Exception:
                log.warning("SECRET_KEY is not a valid Fernet key — generating one")
        if SECRET_PATH.exists():
            return Fernet(SECRET_PATH.read_bytes().strip())
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        SECRET_PATH.write_bytes(key)
        SECRET_PATH.chmod(0o600)
        log.info("Generated new encryption key at %s", SECRET_PATH)
        return Fernet(key)

    def encrypt(text: str) -> str:
        if not text:
            return text
        return _get_fernet().encrypt(text.encode()).decode()

    def decrypt(text: str) -> str:
        if not text:
            return text
        try:
            return _get_fernet().decrypt(text.encode()).decode()
        except (InvalidToken, Exception):
            return text  # plaintext fallback (e.g. migrated from old store)

except ImportError:
    log.warning("cryptography not installed — API keys stored in plaintext")

    def encrypt(text: str) -> str:  # type: ignore[misc]
        return text

    def decrypt(text: str) -> str:  # type: ignore[misc]
        return text


# ── DB helpers ────────────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_providers (
                provider    TEXT PRIMARY KEY,
                api_key     TEXT DEFAULT '',
                model       TEXT DEFAULT '',
                base_url    TEXT DEFAULT '',
                is_active   INTEGER DEFAULT 0,
                updated_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS db_connections (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL DEFAULT 'default',
                db_type           TEXT NOT NULL DEFAULT 'sqlite',
                url               TEXT DEFAULT '',
                mode              TEXT DEFAULT 'read-only',
                allow_destructive INTEGER DEFAULT 0,
                is_active         INTEGER DEFAULT 1,
                created_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_sessions (
                id                TEXT PRIMARY KEY,
                name              TEXT DEFAULT '',
                db_connection_id  INTEGER,
                created_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            );
        """)
        # Migration: add db_connection_id to chat_sessions if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()]
        if "db_connection_id" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN db_connection_id INTEGER")


# ── LLM providers ─────────────────────────────────────────────────────────────

ALL_PROVIDERS = ["openai", "anthropic", "deepseek", "gemini", "groq", "mistral", "ollama"]


def get_all_llm_providers() -> list[dict]:
    """Return one row per known provider, merging DB state with defaults."""
    with _connect() as conn:
        rows = {
            r["provider"]: dict(r) for r in conn.execute("SELECT * FROM llm_providers").fetchall()
        }
    result = []
    for p in ALL_PROVIDERS:
        row = rows.get(
            p, {"provider": p, "api_key": "", "model": "", "base_url": "", "is_active": 0}
        )
        if row.get("api_key"):
            row["api_key"] = decrypt(row["api_key"])
        result.append(row)
    return result


def get_llm_provider(provider: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM llm_providers WHERE provider = ?", (provider,)).fetchone()
    if not row:
        return {"provider": provider, "api_key": "", "model": "", "base_url": "", "is_active": 0}
    d = dict(row)
    if d.get("api_key"):
        d["api_key"] = decrypt(d["api_key"])
    return d


def save_llm_provider(
    provider: str, api_key: str = "", model: str = "", base_url: str = ""
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT api_key FROM llm_providers WHERE provider = ?", (provider,)
        ).fetchone()
        if existing:
            # Keep existing encrypted key if no new key provided
            stored_key = encrypt(api_key) if api_key else (existing["api_key"] or "")
            conn.execute(
                "UPDATE llm_providers SET api_key=?, model=?, base_url=?,"
                " updated_at=? WHERE provider=?",
                (stored_key, model, base_url, now, provider),
            )
        else:
            conn.execute(
                "INSERT INTO llm_providers"
                " (provider, api_key, model, base_url, is_active, updated_at)"
                " VALUES (?,?,?,?,0,?)",
                (provider, encrypt(api_key) if api_key else "", model, base_url, now),
            )


def set_active_llm_provider(provider: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE llm_providers SET is_active = 0")
        conn.execute("UPDATE llm_providers SET is_active = 1 WHERE provider = ?", (provider,))


def get_active_llm_provider() -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM llm_providers WHERE is_active = 1").fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("api_key"):
        d["api_key"] = decrypt(d["api_key"])
    return d


# ── DB connection ─────────────────────────────────────────────────────────────


def get_all_db_connections() -> list[dict]:
    """Return all registered DB connections (decrypted)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM db_connections ORDER BY is_active DESC, id ASC"
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("url"):
            d["url"] = decrypt(d["url"])
        result.append(d)
    return result


def get_db_connection(connection_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM db_connections WHERE id = ?", (connection_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("url"):
        d["url"] = decrypt(d["url"])
    return d


def get_active_db_connection() -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM db_connections WHERE is_active = 1").fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("url"):
        d["url"] = decrypt(d["url"])
    return d


def save_db_connection(
    name: str = "",
    url: str = "",
    mode: str = "read-only",
    allow_destructive: bool = False,
    connection_id: int | None = None,
) -> int:
    """Create or update a DB connection. Returns the connection id."""
    now = datetime.now(timezone.utc).isoformat()
    db_type = _detect_db_type(url)
    with _connect() as conn:
        if connection_id:
            existing = conn.execute(
                "SELECT url FROM db_connections WHERE id = ?", (connection_id,)
            ).fetchone()
            new_url = encrypt(url) if url else (existing["url"] if existing else "")
            conn.execute(
                "UPDATE db_connections SET name=?, db_type=?, url=?, mode=?, allow_destructive=?"
                " WHERE id=?",
                (name or "default", db_type, new_url, mode, int(allow_destructive), connection_id),
            )
            return connection_id
        else:
            cur = conn.execute(
                "INSERT INTO db_connections"
                " (name, db_type, url, mode, allow_destructive, is_active, created_at)"
                " VALUES (?,?,?,?,?,0,?)",
                (
                    name or "default",
                    db_type,
                    encrypt(url) if url else "",
                    mode,
                    int(allow_destructive),
                    now,
                ),
            )
            return cur.lastrowid


def set_active_db_connection(connection_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE db_connections SET is_active = 0")
        conn.execute("UPDATE db_connections SET is_active = 1 WHERE id = ?", (connection_id,))


def delete_db_connection(connection_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM db_connections WHERE id = ?", (connection_id,))


def save_db_settings(url: str = "", mode: str = "", allow_destructive: bool = False) -> None:
    """Legacy: save settings for the active connection (or create one)."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        existing = conn.execute("SELECT id, url FROM db_connections WHERE is_active = 1").fetchone()
        if existing:
            new_url = encrypt(url) if url else (existing["url"] or "")
            # Only update db_type if a new URL was provided
            updates = "UPDATE db_connections SET url=?, mode=?, allow_destructive=?"
            params: list = [new_url, mode or "read-only", int(allow_destructive)]
            if url:
                updates += ", db_type=?"
                params.append(_detect_db_type(url))
            updates += " WHERE is_active=1"
            conn.execute(updates, params)
        else:
            conn.execute(
                "INSERT INTO db_connections"
                " (name, db_type, url, mode, allow_destructive, is_active, created_at)"
                " VALUES ('default',?,?,?,?,1,?)",
                (
                    _detect_db_type(url),
                    encrypt(url) if url else "",
                    mode or "read-only",
                    int(allow_destructive),
                    now,
                ),
            )


def _detect_db_type(url: str) -> str:
    url_lower = url.lower()
    if "postgresql" in url_lower or "postgres" in url_lower:
        return "postgresql"
    if "mysql" in url_lower:
        return "mysql"
    return "sqlite"


def get_app_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def save_app_settings(settings: dict[str, str]) -> None:
    with _connect() as conn:
        for key, value in settings.items():
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


# ── Chat sessions ────────────────────────────────────────────────────────────


def create_chat_session(name: str = "", db_connection_id: int | None = None) -> str:
    import uuid

    session_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (id, name, db_connection_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (session_id, name or f"Chat {now[:10]}", db_connection_id, now),
        )
    return session_id


def list_chat_sessions() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM chat_sessions ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_chat_session(session_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def rename_chat_session(session_id: str, name: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE chat_sessions SET name = ? WHERE id = ?", (name, session_id))


def delete_chat_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))


def get_chat_messages(session_id: str, limit: int = 0) -> list[dict]:
    with _connect() as conn:
        if limit > 0:
            rows = conn.execute(
                "SELECT role, content FROM ("
                "  SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def add_chat_message(session_id: str, role: str, content: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now),
        )


def clear_chat_messages(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))


def reset_all() -> None:
    with _connect() as conn:
        conn.executescript("""
            DELETE FROM llm_providers;
            DELETE FROM db_connections;
            DELETE FROM app_settings;
        """)
