import json
from typing import Callable

import httpx
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import settings_db
from app.assistant.service import _PROVIDER_BASE_URLS, AssistantService
from app.config import Config
from app.logging import QueryLogger
from app.mcp.registry import ToolRegistry

templates = Jinja2Templates(directory="app/web/templates")


RuntimeState = tuple[Config, ToolRegistry]


def build_router(
    logger: QueryLogger,
    get_runtime_state: Callable[[], RuntimeState],
    reload_config: Callable[[], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def current() -> RuntimeState:
        return get_runtime_state()

    def current_config() -> Config:
        return current()[0]

    def current_registry() -> ToolRegistry:
        return current()[1]

    def current_assistant() -> AssistantService:
        cfg, reg = current()
        return AssistantService(cfg, reg)

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request):
        cfg = current_config()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "mode": cfg.mode,
                "db_url": cfg.db_url,
            },
        )

    @router.get("/sandbox", response_class=HTMLResponse)
    def sandbox(request: Request):
        cfg = current_config()
        return templates.TemplateResponse(
            request,
            "sandbox.html",
            {"request": request, "mode": cfg.mode},
        )

    @router.post("/sandbox/run", response_class=HTMLResponse)
    def sandbox_run(request: Request, sql: str = Form(...)):
        result = current_registry().call("sql.query", {"sql": sql})
        return templates.TemplateResponse(
            request,
            "partials/result.html",
            {"request": request, "result": result, "sql": sql},
        )

    @router.get("/schema", response_class=HTMLResponse)
    def schema(request: Request):
        result = current_registry().call("sql.schema", {})
        schema_data = result.get("schema", {})
        mermaid = _build_mermaid_er(schema_data)
        return templates.TemplateResponse(
            request,
            "schema.html",
            {"request": request, "schema": schema_data, "mermaid": mermaid},
        )

    @router.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        entries = logger.list()
        return templates.TemplateResponse(
            request,
            "history.html",
            {"request": request, "entries": entries},
        )

    @router.get("/design", response_class=HTMLResponse)
    def design(request: Request):
        cfg = current_config()
        return templates.TemplateResponse(
            request,
            "design.html",
            {"request": request, "mode": cfg.mode},
        )

    @router.post("/design/diff", response_class=HTMLResponse)
    def design_diff(request: Request, desired: str = Form(...)):
        try:
            desired_schema = json.loads(desired)
        except json.JSONDecodeError:
            return templates.TemplateResponse(
                request,
                "partials/diff.html",
                {"request": request, "result": {"error": "Invalid JSON"}},
            )
        result = current_registry().call("db.schema.diff", {"desired_schema": desired_schema})
        return templates.TemplateResponse(
            request,
            "partials/diff.html",
            {"request": request, "result": result},
        )

    @router.post("/design/plan", response_class=HTMLResponse)
    def design_plan(
        request: Request, desired: str = Form(...), destructive: str | None = Form(None)
    ):
        try:
            desired_schema = json.loads(desired)
        except json.JSONDecodeError:
            return templates.TemplateResponse(
                request,
                "partials/plan.html",
                {"request": request, "result": {"error": "Invalid JSON"}},
            )
        result = current_registry().call(
            "db.migrate.plan",
            {"desired_schema": desired_schema, "destructive": bool(destructive)},
        )
        return templates.TemplateResponse(
            request,
            "partials/plan.html",
            {"request": request, "result": result},
        )

    @router.post("/design/plan-apply", response_class=HTMLResponse)
    def design_plan_apply(
        request: Request,
        desired: str = Form(...),
        destructive: str | None = Form(None),
        dry_run: str | None = Form(None),
    ):
        try:
            desired_schema = json.loads(desired)
        except json.JSONDecodeError:
            return templates.TemplateResponse(
                request,
                "partials/migrate.html",
                {"request": request, "result": {"error": "Invalid JSON"}},
            )
        result = current_registry().call(
            "db.migrate.plan_apply",
            {
                "desired_schema": desired_schema,
                "destructive": bool(destructive),
                "dry_run": bool(dry_run),
            },
        )
        return templates.TemplateResponse(
            request,
            "partials/migrate.html",
            {"request": request, "result": result},
        )

    @router.post("/design/apply", response_class=HTMLResponse)
    def design_apply(request: Request, sql: str = Form(...)):
        result = current_registry().call("db.apply", {"sql": sql})
        return templates.TemplateResponse(
            request,
            "partials/apply.html",
            {"request": request, "result": result},
        )

    @router.post("/design/migrate", response_class=HTMLResponse)
    def design_migrate(request: Request, sql: str = Form(...), dry_run: str | None = Form(None)):
        result = current_registry().call("db.migrate", {"sql": sql, "dry_run": bool(dry_run)})
        return templates.TemplateResponse(
            request,
            "partials/migrate.html",
            {"request": request, "result": result},
        )

    @router.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        cfg = current_config()
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "request": request,
                "mode": cfg.mode,
                "config": cfg,
                "providers": settings_db.get_all_llm_providers(),
                "db_conn": settings_db.get_active_db_connection(),
            },
        )

    @router.post("/settings/llm/{provider}", response_class=HTMLResponse)
    async def settings_save_llm(
        request: Request,
        provider: str,
        api_key: str = Form(""),
        model: str = Form(""),
        base_url: str = Form(""),
    ):
        settings_db.save_llm_provider(provider, api_key=api_key, model=model, base_url=base_url)
        if reload_config:
            reload_config()
        return RedirectResponse(f"/settings?saved={provider}", status_code=303)

    @router.post("/settings/llm/{provider}/activate", response_class=HTMLResponse)
    async def settings_activate_llm(request: Request, provider: str):
        settings_db.set_active_llm_provider(provider)
        if reload_config:
            reload_config()
        return RedirectResponse("/settings?activated=1", status_code=303)

    @router.post("/settings/db", response_class=HTMLResponse)
    async def settings_save_db(
        request: Request,
        db_url: str = Form(""),
        db_mode: str = Form(""),
        allow_destructive: str | None = Form(None),
        timeout_ms: int = Form(5000),
        limit_default: int = Form(100),
        enable_explanations: str | None = Form(None),
    ):
        settings_db.save_db_settings(
            url=db_url,
            mode=db_mode,
            allow_destructive=allow_destructive is not None,
        )
        settings_db.save_app_settings(
            {
                "timeout_ms": str(timeout_ms),
                "limit_default": str(limit_default),
                "enable_explanations": "true" if enable_explanations is not None else "false",
            }
        )
        if reload_config:
            reload_config()
        return RedirectResponse("/settings?saved=db", status_code=303)

    @router.post("/settings/chat", response_class=HTMLResponse)
    async def settings_save_chat(
        request: Request,
        chat_history_enabled: str | None = Form(None),
        chat_history_limit: int = Form(10),
    ):
        settings_db.save_app_settings(
            {
                "chat_history_enabled": "true" if chat_history_enabled is not None else "false",
                "chat_history_limit": str(chat_history_limit),
            }
        )
        if reload_config:
            reload_config()
        return RedirectResponse("/settings?saved=chat", status_code=303)

    @router.post("/settings/reset", response_class=HTMLResponse)
    def settings_reset(request: Request):
        settings_db.reset_all()
        if reload_config:
            reload_config()
        return RedirectResponse("/settings?reset=1", status_code=303)

    @router.get("/settings/models", response_class=HTMLResponse)
    async def settings_fetch_models(
        request: Request,
        provider: str = Query(""),
        llm_api_key: str = Query(""),
        llm_base_url: str = Query(""),
    ):
        if not provider:
            return HTMLResponse('<option value="">Select a provider first</option>')

        cfg = current_config()
        provider_settings = settings_db.get_llm_provider(provider)
        api_key = llm_api_key or provider_settings.get("api_key", "")
        base_url = (
            llm_base_url
            or provider_settings.get("base_url")
            or _PROVIDER_BASE_URLS.get(provider, "")
        )
        models = await _fetch_provider_models(provider, api_key, base_url)
        if isinstance(models, str):
            return HTMLResponse(f'<option value="">Unavailable: {models}</option>')
        if not models:
            return HTMLResponse('<option value="">No models returned</option>')

        current_model = provider_settings.get("model") or ""
        if provider == cfg.llm_provider and not current_model:
            current_model = cfg.llm_model or ""
        opts = "\n".join(
            f'<option value="{m}" {"selected" if m == current_model else ""}>{m}</option>'
            for m in models
        )
        return HTMLResponse(f'<option value="">Choose a live model</option>\n{opts}')

    @router.get("/chat", response_class=HTMLResponse)
    @router.get("/chat/{session_id}", response_class=HTMLResponse)
    def chat_page(request: Request, session_id: str = ""):
        cfg = current_config()
        sessions = settings_db.list_chat_sessions()
        if session_id:
            active_session = settings_db.get_chat_session(session_id)
        else:
            active_session = sessions[0] if sessions else None
            session_id = active_session["id"] if active_session else ""
        raw_messages = settings_db.get_chat_messages(session_id) if session_id else []

        # Parse assistant JSON content for rich rendering
        messages = []
        for m in raw_messages:
            if m["role"] == "assistant":
                try:
                    parsed = json.loads(m["content"])
                    messages.append({"role": "assistant", "parsed": parsed})
                except (json.JSONDecodeError, TypeError):
                    messages.append({"role": "assistant", "parsed": {"explanation": m["content"]}})
            else:
                messages.append(m)

        return templates.TemplateResponse(
            request,
            "chat.html",
            {
                "request": request,
                "mode": cfg.mode,
                "llm_provider": cfg.llm_provider,
                "llm_model": cfg.llm_model,
                "sessions": sessions,
                "active_session": active_session,
                "session_id": session_id,
                "messages": messages,
                "chat_history_enabled": cfg.chat_history_enabled,
            },
        )

    @router.post("/chat/new", response_class=HTMLResponse)
    def chat_new(request: Request):
        sid = settings_db.create_chat_session()
        return RedirectResponse(f"/chat/{sid}", status_code=303)

    @router.post("/chat/{session_id}/delete", response_class=HTMLResponse)
    def chat_delete(request: Request, session_id: str):
        settings_db.delete_chat_session(session_id)
        return RedirectResponse("/chat", status_code=303)

    @router.post("/chat/{session_id}/rename", response_class=HTMLResponse)
    def chat_rename(request: Request, session_id: str, name: str = Form("")):
        if name.strip():
            settings_db.rename_chat_session(session_id, name.strip())
        return RedirectResponse(f"/chat/{session_id}", status_code=303)

    @router.post("/chat/send", response_class=HTMLResponse)
    async def chat_send(
        request: Request,
        message: str = Form(...),
        session_id: str = Form(""),
    ):
        cfg = current_config()
        registry = current_registry()

        # Auto-create session if none
        if not session_id:
            session_id = settings_db.create_chat_session()

        msg_lower = message.strip().lower()

        def _save_assistant(sid: str, data: dict) -> None:
            """Save assistant response as JSON for rich replay."""
            settings_db.add_chat_message(sid, "assistant", json.dumps(data, ensure_ascii=False))

        # /run — execute SQL directly
        if msg_lower.startswith("/run "):
            sql = message.strip()[5:].strip()
            result = registry.call("sql.query", {"sql": sql})
            ctx = {"sql": sql, "query_result": result, "is_direct": True}
            settings_db.add_chat_message(session_id, "user", message)
            _save_assistant(session_id, ctx)
            return templates.TemplateResponse(
                request,
                "partials/chat_message.html",
                {"request": request, "msg": message, **ctx},
            )

        # /explain — EXPLAIN plan
        if msg_lower.startswith("/explain "):
            sql = message.strip()[9:].strip()
            result = registry.call("sql.explain", {"sql": sql})
            ctx = {"query_result": result, "is_direct": True, "command": "explain"}
            settings_db.add_chat_message(session_id, "user", message)
            _save_assistant(session_id, ctx)
            return templates.TemplateResponse(
                request,
                "partials/chat_message.html",
                {"request": request, "msg": message, **ctx},
            )

        # /schema — full schema
        if msg_lower == "/schema":
            result = registry.call("sql.schema", {})
            ctx = {"query_result": result, "is_direct": True, "command": "schema"}
            settings_db.add_chat_message(session_id, "user", message)
            _save_assistant(session_id, ctx)
            return templates.TemplateResponse(
                request,
                "partials/chat_message.html",
                {"request": request, "msg": message, **ctx},
            )

        # /tables — list table names
        if msg_lower == "/tables":
            result = registry.call("sql.schema", {})
            tables = list(result.get("schema", {}).get("tables", {}).keys())
            ctx = {"tables": tables, "is_direct": True, "command": "tables"}
            settings_db.add_chat_message(session_id, "user", message)
            _save_assistant(session_id, ctx)
            return templates.TemplateResponse(
                request,
                "partials/chat_message.html",
                {"request": request, "msg": message, **ctx},
            )

        # Save user message
        settings_db.add_chat_message(session_id, "user", message)

        # Build history for LLM context (extract text from JSON assistant msgs)
        history: list[dict] = []
        if cfg.chat_history_enabled:
            raw_hist = settings_db.get_chat_messages(session_id, limit=cfg.chat_history_limit)
            # Remove the last message (current user message) from context
            if raw_hist and raw_hist[-1]["role"] == "user":
                raw_hist = raw_hist[:-1]
            for h in raw_hist:
                if h["role"] == "assistant":
                    try:
                        parsed = json.loads(h["content"])
                        text = parsed.get("explanation") or parsed.get("sql") or h["content"]
                    except (json.JSONDecodeError, TypeError):
                        text = h["content"]
                    history.append({"role": "assistant", "content": text})
                else:
                    history.append(h)

        result = await current_assistant().chat(message, history)

        # Save full assistant response as JSON
        _save_assistant(
            session_id,
            {
                "thought": result.get("thought", ""),
                "sql": result.get("sql"),
                "explanation": result.get("explanation", ""),
                "error": result.get("error"),
                "query_result": result.get("query_result"),
            },
        )

        # Auto-rename session from first message
        session = settings_db.get_chat_session(session_id)
        if session and session["name"].startswith("Chat 20"):
            short_name = message[:40] + ("..." if len(message) > 40 else "")
            settings_db.rename_chat_session(session_id, short_name)

        return templates.TemplateResponse(
            request,
            "partials/chat_message.html",
            {"request": request, "msg": message, **result},
        )

    @router.post("/chat/{session_id}/clear", response_class=HTMLResponse)
    def chat_clear(request: Request, session_id: str):
        settings_db.clear_chat_messages(session_id)
        return RedirectResponse(f"/chat/{session_id}", status_code=303)

    return router


async def _fetch_provider_models(provider: str, api_key: str, base_url: str) -> list[str] | str:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider == "ollama":
                r = await client.get(f"{base_url}/api/tags")
                if r.status_code != 200:
                    return f"HTTP {r.status_code}"
                return [m["name"] for m in r.json().get("models", [])]

            if provider == "gemini":
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                )
                if r.status_code != 200:
                    return f"HTTP {r.status_code}"
                return [
                    m["name"].replace("models/", "")
                    for m in r.json().get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]

            if provider == "anthropic":
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
                if r.status_code != 200:
                    return f"HTTP {r.status_code}"
                return [m["id"] for m in r.json().get("data", [])]

            r = await client.get(
                f"{base_url}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if r.status_code != 200:
                return f"HTTP {r.status_code}"
            models = sorted(m["id"] for m in r.json().get("data", []))
            if provider == "openai":
                models = [
                    m for m in models if any(p in m for p in ["gpt-", "o1", "o3", "o4", "chatgpt"])
                ]
            return models
    except Exception as exc:
        return str(exc)


def _build_mermaid_er(schema: dict) -> str:
    """Generate a Mermaid erDiagram string from schema introspection data."""
    lines = ["erDiagram"]
    fk_relations: list[str] = []

    for table, info in schema.items():
        lines.append(f"    {table} {{")
        for col in info.get("columns", []):
            col_type = col["type"].replace(" ", "_").upper()
            pk = "PK" if col["name"] == "id" else ""
            fk = ""
            for fk_info in info.get("foreign_keys", []):
                if col["name"] in fk_info.get("constrained_columns", []):
                    fk = "FK"
                    ref_table = fk_info["referred_table"]
                    ref_cols = fk_info.get("referred_columns", [])
                    ref_col = ref_cols[0] if ref_cols else "?"
                    label = f"{table}.{col['name']} -> {ref_table}.{ref_col}"
                    fk_relations.append(f'    {ref_table} ||--o{{ {table} : "{label}"')
                    break
            key_marker = f"{pk},{fk}" if pk and fk else (pk or fk)
            key_str = f" {key_marker}" if key_marker else ""
            lines.append(f"        {col_type} {col['name']}{key_str}")
        lines.append("    }")

    for rel in fk_relations:
        lines.append(rel)

    return "\n".join(lines)
