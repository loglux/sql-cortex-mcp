from dataclasses import dataclass
from datetime import UTC, datetime
from typing import List


@dataclass
class QueryLogEntry:
    ts: str
    tool: str
    sql: str
    ok: bool
    elapsed_ms: int
    rows: int
    error: str | None


class QueryLogger:
    def __init__(self) -> None:
        self._entries: List[QueryLogEntry] = []

    def add(self, entry: QueryLogEntry) -> None:
        self._entries.append(entry)

    def list(self, limit: int = 200) -> List[QueryLogEntry]:
        return list(reversed(self._entries[-limit:]))


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
