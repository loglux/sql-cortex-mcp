import datetime
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.sql.policy import enforce_limit, is_allowed, is_read_query


def _coerce_value(val: Any) -> Any:
    """Convert DB-specific types to JSON-safe Python primitives."""
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


class SQLExecutor:
    def __init__(self, db_url: str) -> None:
        self.engine: Engine = create_engine(db_url)

    def get_version(self) -> str:
        """Return the database server version string."""
        dialect = self.engine.dialect.name
        try:
            with self.engine.connect() as conn:
                if dialect == "sqlite":
                    row = conn.execute(text("SELECT sqlite_version()")).fetchone()
                else:
                    row = conn.execute(text("SELECT VERSION()")).fetchone()
                if row:
                    return str(row[0])
        except SQLAlchemyError:
            pass
        return ""

    def execute(
        self,
        sql: str,
        mode: str,
        limit_default: int,
        timeout_ms: int | None = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], int, str | None]:
        if not is_allowed(sql, mode):
            return [], [], 0, "Query not allowed by policy"

        safe_sql = enforce_limit(sql, limit_default) if is_read_query(sql) else sql
        start = time.time()

        def cleanup() -> None:
            pass

        try:
            with self.engine.begin() as conn:
                cleanup = self._apply_timeout(conn, timeout_ms)
                result = conn.execute(text(safe_sql))
                elapsed_ms = int((time.time() - start) * 1000)
                if result.returns_rows:
                    rows = result.fetchall()
                    columns = list(result.keys())
                    parsed = [
                        {col: _coerce_value(val) for col, val in zip(columns, row)} for row in rows
                    ]
                    return parsed, columns, elapsed_ms, None
        except SQLAlchemyError as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            return [], [], elapsed_ms, str(exc)
        finally:
            cleanup()

        elapsed_ms = int((time.time() - start) * 1000)
        return [], [], elapsed_ms, None

    def _apply_timeout(self, conn: Connection, timeout_ms: int | None) -> Callable[[], None]:
        if not timeout_ms or timeout_ms <= 0:
            return lambda: None

        dialect = conn.engine.dialect.name
        if dialect == "postgresql":
            conn.execute(
                text("SET LOCAL statement_timeout = :timeout_ms"), {"timeout_ms": timeout_ms}
            )
            return lambda: None

        if dialect == "sqlite":
            raw_conn = conn.connection.driver_connection
            deadline = time.monotonic() + (timeout_ms / 1000)

            def _progress_handler() -> int:
                return 1 if time.monotonic() >= deadline else 0

            raw_conn.set_progress_handler(_progress_handler, 1000)

            def _cleanup() -> None:
                raw_conn.set_progress_handler(None, 0)

            return _cleanup

        return lambda: None
