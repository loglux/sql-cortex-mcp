import asyncio
import collections
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Any, Dict, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import Config
from app.logging import QueryLogger
from app.mcp.prompts import PromptRegistry
from app.mcp.registry import ToolRegistry
from app.mcp.resources import ResourceRegistry
from app.mcp.tools import build_tools
from app.session_db import SessionDBManager
from app.web.routes import build_router

load_dotenv()

# ── Rate limiting ──────────────────────────────────────────────────────────────
# Disabled automatically when running under pytest; configure via env vars.
_testing = "pytest" in sys.modules
_MCP_RATE_LIMIT = 0 if _testing else int(os.getenv("MCP_RATE_LIMIT", "60"))
_SSE_MAX_PER_IP = 0 if _testing else int(os.getenv("SSE_MAX_PER_IP", "10"))


class _SlidingWindowLimiter:
    """Async-safe sliding window rate limiter. limit=0 disables checking."""

    def __init__(self, limit: int, window: int) -> None:
        self._limit = limit
        self._window = window
        self._hits: dict[str, collections.deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> bool:
        if not self._limit:
            return True
        now = time.monotonic()
        async with self._lock:
            dq = self._hits.setdefault(key, collections.deque())
            cutoff = now - self._window
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._limit:
                return False
            dq.append(now)
            return True


_mcp_limiter = _SlidingWindowLimiter(limit=_MCP_RATE_LIMIT, window=60)

logger = QueryLogger()
session_db_mgr = SessionDBManager("")


def _build_app_state() -> tuple[Config, ToolRegistry]:
    global session_db_mgr
    cfg = Config.load()
    session_db_mgr = SessionDBManager(cfg.db_url)
    reg = ToolRegistry()
    for tool_def, handler in build_tools(cfg, logger, session_db_mgr):
        reg.register(tool_def, handler)
    return cfg, reg


config, registry = _build_app_state()
resources = ResourceRegistry(config)
prompts = PromptRegistry(config)


SUPPORTED_MCP_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}

SESSION_TTL = 600  # seconds before idle session expires
SESSION_GC_INTERVAL = 60  # seconds between garbage collection sweeps
SSE_KEEPALIVE_INTERVAL = 15  # seconds between SSE keepalive pings

_sessions: Dict[str, Tuple[asyncio.Queue[str], float, str]] = {}
_sse_ip_counts: Dict[str, int] = {}
_sessions_lock = asyncio.Lock()
_reload_lock = asyncio.Lock()


async def reload_config() -> None:
    """Reload config + registry after settings change. Called from settings route."""
    async with _reload_lock:
        global config, registry, resources, prompts
        config, registry = _build_app_state()
        resources = ResourceRegistry(config)
        prompts = PromptRegistry(config)


def get_runtime_state() -> tuple[Config, ToolRegistry]:
    return config, registry


async def _create_session(ip: str) -> str | None:
    session_id = str(uuid.uuid4())
    async with _sessions_lock:
        if _SSE_MAX_PER_IP and _sse_ip_counts.get(ip, 0) >= _SSE_MAX_PER_IP:
            return None
        _sessions[session_id] = (asyncio.Queue(), time.time(), ip)
        _sse_ip_counts[ip] = _sse_ip_counts.get(ip, 0) + 1
    return session_id


async def _get_session(session_id: str) -> asyncio.Queue[str] | None:
    async with _sessions_lock:
        entry = _sessions.get(session_id)
        if not entry:
            return None
        queue, _, ip = entry
        _sessions[session_id] = (queue, time.time(), ip)
        return queue


async def _remove_session(session_id: str) -> None:
    async with _sessions_lock:
        entry = _sessions.pop(session_id, None)
        if entry:
            ip = entry[2]
            _sse_ip_counts[ip] = max(0, _sse_ip_counts.get(ip, 1) - 1)
    session_db_mgr.clear_session(session_id)


async def _enqueue(session_id: str, payload: Dict[str, Any]) -> bool:
    queue = await _get_session(session_id)
    if queue is None:
        return False
    await queue.put(json.dumps(payload))
    return True


async def _gc_sessions() -> None:
    while True:
        await asyncio.sleep(SESSION_GC_INTERVAL)
        now = time.time()
        async with _sessions_lock:
            expired = [
                session_id
                for session_id, (_, last_seen, _ip) in _sessions.items()
                if now - last_seen > SESSION_TTL
            ]
            for session_id in expired:
                entry = _sessions.pop(session_id, None)
                if entry:
                    ip = entry[2]
                    _sse_ip_counts[ip] = max(0, _sse_ip_counts.get(ip, 1) - 1)
                session_db_mgr.clear_session(session_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    gc_task = asyncio.create_task(_gc_sessions())
    try:
        yield
    finally:
        gc_task.cancel()
        with suppress(asyncio.CancelledError):
            await gc_task


app = FastAPI(title="SQL Cortex MCP", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

if config.enable_ui:
    app.include_router(build_router(logger, get_runtime_state, reload_config))


def _jsonrpc_response(result: Any, req_id: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(code: int, message: str, req_id: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _response(payload: Dict[str, Any], status_code: int = 200) -> Response:
    return Response(
        status_code=status_code,
        media_type="application/json",
        headers={"MCP-Protocol-Version": "2025-11-25"},
        content=json.dumps(payload),
    )


def _check_origin(request: Request) -> Tuple[bool, str | None]:
    origin = request.headers.get("origin")
    if not origin:
        return True, None
    if origin in config.allowed_origins:
        return True, None
    return False, origin


@app.post("/mcp")
async def mcp(request: Request) -> Response:
    ip = request.client.host if request.client else "unknown"
    if not await _mcp_limiter.check(ip):
        return Response(status_code=429, content="Rate limit exceeded")

    ok, origin = _check_origin(request)
    if not ok:
        return Response(status_code=403, content="Invalid Origin")

    proto = request.headers.get("mcp-protocol-version")
    if proto and proto not in SUPPORTED_MCP_VERSIONS:
        return Response(status_code=400, content="Unsupported MCP-Protocol-Version")

    try:
        payload = await request.json()
    except Exception:
        err = _jsonrpc_error(-32700, "Parse error", None)
        return _response(err, status_code=400)

    if isinstance(payload, list):
        err = _jsonrpc_error(-32600, "Batching not supported", None)
        return _response(err, status_code=400)

    if not isinstance(payload, dict):
        err = _jsonrpc_error(-32600, "Invalid Request", None)
        return _response(err, status_code=400)

    req_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}
    session_id = request.headers.get("mcp-session-id")

    if method is None and ("result" in payload or "error" in payload):
        return Response(status_code=202)

    if not method:
        err = _jsonrpc_error(-32600, "Invalid Request", req_id)
        return _response(err, status_code=400)

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        if requested_version in SUPPORTED_MCP_VERSIONS:
            negotiated_version = requested_version
        else:
            negotiated_version = "2025-11-25"
        result = {
            "protocolVersion": negotiated_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {"name": "sql-cortex-mcp", "version": "0.1.0"},
        }
        response_payload = _jsonrpc_response(result, req_id)
        if session_id and await _enqueue(session_id, response_payload):
            return Response(status_code=202)
        return _response(response_payload)

    if method == "notifications/initialized":
        return Response(status_code=202)

    if req_id is None and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "tools/list":
        result = registry.list_tools()
        response_payload = _jsonrpc_response(result, req_id)
        if session_id and await _enqueue(session_id, response_payload):
            return Response(status_code=202)
        return _response(response_payload)

    if method == "resources/list":
        result = resources.list_resources()
        response_payload = _jsonrpc_response(result, req_id)
        if session_id and await _enqueue(session_id, response_payload):
            return Response(status_code=202)
        return _response(response_payload)

    if method == "resources/read":
        uri = params.get("uri")
        if not uri:
            err = _jsonrpc_error(-32602, "Missing resource uri", req_id)
            return _response(err, status_code=400)
        result = resources.read_resource(uri)
        if result is None:
            err = _jsonrpc_error(-32002, f"Resource not found: {uri}", req_id)
            if session_id and await _enqueue(session_id, err):
                return Response(status_code=202)
            return _response(err)
        response_payload = _jsonrpc_response(result, req_id)
        if session_id and await _enqueue(session_id, response_payload):
            return Response(status_code=202)
        return _response(response_payload)

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not tool_name:
            err = _jsonrpc_error(-32602, "Missing tool name", req_id)
            return _response(err, status_code=400)
        if not registry.has_tool(tool_name):
            err = _jsonrpc_error(-32602, f"Unknown tool: {tool_name}", req_id)
            if session_id and await _enqueue(session_id, err):
                return Response(status_code=202)
            return _response(err)
        arguments["_context"] = {"session_id": session_id}
        tool_result = registry.call(tool_name, arguments)
        is_error = bool(tool_result.get("error"))
        content = [
            {
                "type": "text",
                "text": json.dumps(tool_result, ensure_ascii=False),
            }
        ]
        result = {"content": content, "structuredContent": tool_result, "isError": is_error}
        response_payload = _jsonrpc_response(result, req_id)
        if session_id and await _enqueue(session_id, response_payload):
            return Response(status_code=202)
        return _response(response_payload)

    if method == "prompts/list":
        result = prompts.list_prompts()
        response_payload = _jsonrpc_response(result, req_id)
        if session_id and await _enqueue(session_id, response_payload):
            return Response(status_code=202)
        return _response(response_payload)

    if method == "prompts/get":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not name:
            err = _jsonrpc_error(-32602, "Missing prompt name", req_id)
            return _response(err, status_code=400)
        result = prompts.get_prompt(name, arguments)
        if result is None:
            err = _jsonrpc_error(-32602, f"Unknown prompt: {name}", req_id)
            if session_id and await _enqueue(session_id, err):
                return Response(status_code=202)
            return _response(err)
        response_payload = _jsonrpc_response(result, req_id)
        if session_id and await _enqueue(session_id, response_payload):
            return Response(status_code=202)
        return _response(response_payload)

    err = _jsonrpc_error(-32601, f"Method not found: {method}", req_id)
    if session_id and await _enqueue(session_id, err):
        return Response(status_code=202)
    return _response(err, status_code=404)


@app.get("/mcp")
async def mcp_stream(request: Request) -> Response:
    ok, _ = _check_origin(request)
    if not ok:
        return Response(status_code=403, content="Invalid Origin")
    ip = request.client.host if request.client else "unknown"
    session_id = await _create_session(ip)
    if session_id is None:
        return Response(status_code=429, content="Too many SSE connections from this IP")
    queue = await _get_session(session_id)

    async def event_stream():
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_INTERVAL)
                    yield f"event: message\ndata: {message}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await _remove_session(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "MCP-Protocol-Version": "2025-11-25",
            "MCP-Session-Id": session_id,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
