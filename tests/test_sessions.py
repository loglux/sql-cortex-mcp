"""Tests for SSE session management (create, get, expire, GC)."""

import asyncio
import time
from unittest.mock import patch

import pytest
from app import main as main_module
from app.main import (
    SESSION_TTL,
    _create_session,
    _enqueue,
    _get_session,
    _remove_session,
    _sessions,
    _sessions_lock,
)

pytestmark = pytest.mark.asyncio


async def _clear_sessions() -> None:
    async with _sessions_lock:
        _sessions.clear()


async def test_create_and_get_session() -> None:
    await _clear_sessions()
    sid = await _create_session()
    assert sid
    queue = await _get_session(sid)
    assert queue is not None


async def test_get_nonexistent_session() -> None:
    await _clear_sessions()
    queue = await _get_session("nonexistent-id")
    assert queue is None


async def test_remove_session() -> None:
    await _clear_sessions()
    sid = await _create_session()
    await _remove_session(sid)
    queue = await _get_session(sid)
    assert queue is None


async def test_remove_nonexistent_session() -> None:
    await _clear_sessions()
    # Should not raise
    await _remove_session("nonexistent-id")


async def test_enqueue_and_dequeue() -> None:
    await _clear_sessions()
    sid = await _create_session()
    ok = await _enqueue(sid, {"test": "payload"})
    assert ok
    queue = await _get_session(sid)
    message = queue.get_nowait()
    assert '"test"' in message


async def test_enqueue_nonexistent_session() -> None:
    await _clear_sessions()
    ok = await _enqueue("nonexistent-id", {"test": "payload"})
    assert not ok


async def test_get_session_updates_last_seen() -> None:
    await _clear_sessions()
    sid = await _create_session()
    async with _sessions_lock:
        _, ts1 = _sessions[sid]
    await asyncio.sleep(0.01)
    await _get_session(sid)
    async with _sessions_lock:
        _, ts2 = _sessions[sid]
    assert ts2 >= ts1


async def test_gc_removes_expired_sessions() -> None:
    await _clear_sessions()
    sid = await _create_session()

    # Manually set last_seen to the past
    async with _sessions_lock:
        queue, _ = _sessions[sid]
        _sessions[sid] = (queue, time.time() - SESSION_TTL - 10)

    # Run one GC cycle (patch sleep to break after first iteration)
    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError

    with patch("app.main.asyncio.sleep", side_effect=_fake_sleep):
        try:
            await main_module._gc_sessions()
        except asyncio.CancelledError:
            pass

    queue = await _get_session(sid)
    assert queue is None  # expired session was removed
