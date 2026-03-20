"""Regression tests for SQL executor."""

import pytest
from app.sql.executor import SQLExecutor
from sqlalchemy import text

DB_URL = "sqlite:///:memory:"


@pytest.fixture
def executor() -> SQLExecutor:
    ex = SQLExecutor(DB_URL)
    with ex.engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob'), (3, 'Charlie')"))
    return ex


def test_select_returns_rows(executor: SQLExecutor) -> None:
    rows, cols, elapsed, err = executor.execute(
        "SELECT * FROM users", mode="read-only", limit_default=100
    )
    assert err is None
    assert len(rows) == 3
    assert "id" in cols
    assert "name" in cols


def test_select_respects_limit(executor: SQLExecutor) -> None:
    rows, cols, elapsed, err = executor.execute(
        "SELECT * FROM users", mode="read-only", limit_default=2
    )
    assert err is None
    assert len(rows) == 2


def test_select_with_explicit_limit_not_doubled(executor: SQLExecutor) -> None:
    rows, _, _, err = executor.execute(
        "SELECT * FROM users LIMIT 1", mode="read-only", limit_default=100
    )
    assert err is None
    assert len(rows) == 1


def test_policy_blocks_insert_in_read_only(executor: SQLExecutor) -> None:
    rows, cols, elapsed, err = executor.execute(
        "INSERT INTO users VALUES (99, 'X')", mode="read-only", limit_default=100
    )
    assert err == "Query not allowed by policy"
    assert rows == []


def test_insert_allowed_in_execute_mode(executor: SQLExecutor) -> None:
    _, _, _, err = executor.execute(
        "INSERT INTO users VALUES (99, 'X')", mode="execute", limit_default=100
    )
    assert err is None
    rows, _, _, _ = executor.execute(
        "SELECT * FROM users WHERE id=99", mode="read-only", limit_default=100
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "X"


def test_elapsed_ms_is_non_negative(executor: SQLExecutor) -> None:
    _, _, elapsed, _ = executor.execute("SELECT 1", mode="read-only", limit_default=100)
    assert elapsed >= 0


def test_invalid_sql_returns_error(executor: SQLExecutor) -> None:
    _, _, _, err = executor.execute(
        "SELECT * FROM nonexistent_table", mode="read-only", limit_default=100
    )
    assert err is not None


def test_timeout_interrupts_sqlite_query(executor: SQLExecutor) -> None:
    with executor.engine.begin() as conn:
        conn.execute(text("CREATE TABLE numbers (n INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "WITH RECURSIVE cnt(x) AS"
                " (SELECT 1 UNION ALL SELECT x + 1 FROM cnt WHERE x < 4000)"
                " INSERT INTO numbers SELECT x FROM cnt"
            )
        )

    _, _, _, err = executor.execute(
        "SELECT count(*) FROM numbers a, numbers b, numbers c",
        mode="read-only",
        limit_default=1,
        timeout_ms=1,
    )
    assert err is not None
