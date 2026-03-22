import os
from dataclasses import dataclass


@dataclass
class Config:
    db_url: str
    mode: str
    limit_default: int
    timeout_ms: int
    enable_ui: bool
    enable_explanations: bool
    allowed_origins: list[str]
    allow_destructive: bool
    # LLM (active provider)
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_base_url: str | None
    openai_api_mode: str
    llm_timeout_ms: int
    chat_history_enabled: bool
    chat_history_limit: int

    @classmethod
    def load(cls) -> "Config":
        """Build config: env vars (install defaults) → settings_db (runtime overrides)."""
        # ── env var defaults ──────────────────────────────────────────────────
        db_url = os.getenv("DB_URL", "sqlite:///./data/dev.db")
        mode = os.getenv("MODE", "read-only").lower()
        limit_default = int(os.getenv("LIMIT_DEFAULT", "100"))
        timeout_ms = int(os.getenv("TIMEOUT_MS", "5000"))
        enable_ui = os.getenv("ENABLE_UI", "true").lower() == "true"
        enable_explanations = os.getenv("ENABLE_EXPLANATIONS", "true").lower() == "true"
        allowed_origins = [
            o.strip()
            for o in os.getenv(
                "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
            ).split(",")
            if o.strip()
        ]
        allow_destructive = os.getenv("ALLOW_DESTRUCTIVE", "false").lower() == "true"
        llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()
        llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        llm_model = os.getenv("LLM_MODEL", "gpt-5.4-mini")
        llm_base_url: str | None = os.getenv("LLM_BASE_URL") or None
        openai_api_mode = os.getenv("OPENAI_API_MODE", "chat").lower()
        llm_timeout_ms = int(os.getenv("LLM_TIMEOUT_MS", "60000"))
        chat_history_enabled = True
        chat_history_limit = 10

        # ── settings DB overrides (runtime, UI-managed) ───────────────────────
        try:
            from app import settings_db

            settings_db.init_db()

            db_conn = settings_db.get_active_db_connection()
            if db_conn:
                if db_conn.get("url"):
                    db_url = db_conn["url"]
                if db_conn.get("mode"):
                    mode = db_conn["mode"].lower()
                allow_destructive = bool(db_conn.get("allow_destructive", allow_destructive))

            # app_settings overrides
            v = settings_db.get_app_setting("timeout_ms")
            if v:
                timeout_ms = int(v)
            v = settings_db.get_app_setting("limit_default")
            if v:
                limit_default = int(v)
            v = settings_db.get_app_setting("enable_ui")
            if v:
                enable_ui = v.lower() == "true"
            v = settings_db.get_app_setting("enable_explanations")
            if v:
                enable_explanations = v.lower() == "true"
            v = settings_db.get_app_setting("llm_timeout_ms")
            if v:
                llm_timeout_ms = int(v)
            v = settings_db.get_app_setting("chat_history_enabled")
            if v:
                chat_history_enabled = v.lower() == "true"
            v = settings_db.get_app_setting("chat_history_limit")
            if v:
                chat_history_limit = int(v)

            llm = settings_db.get_active_llm_provider()
            if llm:
                llm_provider = llm["provider"]
                if llm.get("api_key"):
                    llm_api_key = llm["api_key"]
                if llm.get("model"):
                    llm_model = llm["model"]
                if llm.get("base_url"):
                    llm_base_url = llm["base_url"] or None

        except Exception:
            pass  # DB not ready yet (e.g. first boot, tests)

        return cls(
            db_url=db_url,
            mode=mode,
            limit_default=limit_default,
            timeout_ms=timeout_ms,
            enable_ui=enable_ui,
            enable_explanations=enable_explanations,
            allowed_origins=allowed_origins,
            allow_destructive=allow_destructive,
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            openai_api_mode=openai_api_mode,
            llm_timeout_ms=llm_timeout_ms,
            chat_history_enabled=chat_history_enabled,
            chat_history_limit=chat_history_limit,
        )

    @property
    def db_display_name(self) -> str:
        """Human-readable DB identifier without credentials."""
        from urllib.parse import urlparse

        try:
            # Normalize: strip driver prefix (mysql+pymysql → mysql)
            url = self.db_url
            if ":///" in url:
                # SQLite-style: sqlite:///./data/dev.db
                return f"{self.db_type} — {url.split('///')[-1]}"
            parsed = urlparse(url)
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            db_name = parsed.path.lstrip("/") if parsed.path else ""
            if host:
                return f"{self.db_type} — {db_name}@{host}{port}"
            return self.db_type
        except Exception:
            return self.db_type

    @property
    def db_type(self) -> str:
        url = self.db_url.lower()
        if "postgresql" in url or "postgres" in url:
            return "PostgreSQL"
        if "mysql" in url:
            return "MySQL"
        if "sqlite" in url:
            return "SQLite"
        return "SQL"

    @classmethod
    def from_env(cls) -> "Config":
        return cls.load()
