import re

READ_ONLY_PREFIXES = ("select", "with", "explain", "show", "describe", "desc")
READ_QUERY_PREFIXES = ("select", "with")


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().lower())


WRITE_OPS_RE = re.compile(
    r"\b(insert|update|delete|create|alter|drop|truncate|grant|revoke|merge|replace)\b"
)
SELECT_INTO_RE = re.compile(r"\bselect\b.*\binto\b", re.DOTALL)
FOR_UPDATE_RE = re.compile(r"\bfor\s+update\b")
EXPLAIN_OPTION_RE = re.compile(r"^\s*\((?:[^)(]|\([^)(]*\))*\)\s*")


def _strip_literals_and_comments(sql: str) -> str:
    out: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                out.append(" ")
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                out.append(" ")
                continue
            i += 1
            continue

        if in_single:
            if ch == "'" and nxt == "'":
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            if ch == '"' and nxt == '"':
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            out.append(" ")
            continue
        if ch == '"':
            in_double = True
            i += 1
            out.append(" ")
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _has_multiple_statements(sql: str) -> bool:
    stripped = _strip_literals_and_comments(sql)
    trimmed = stripped.strip().rstrip(";").strip()
    return ";" in trimmed


def _contains_write_operations(normalized_sql: str) -> bool:
    return bool(WRITE_OPS_RE.search(normalized_sql))


def _is_explain_readonly(normalized_sql: str) -> bool:
    inner = normalized_sql[len("explain") :].strip()
    while inner.startswith("analyze "):
        inner = inner[len("analyze ") :].strip()
    while inner.startswith("verbose "):
        inner = inner[len("verbose ") :].strip()

    # EXPLAIN (<options>) ...
    while inner.startswith("("):
        match = EXPLAIN_OPTION_RE.match(inner)
        if not match:
            break
        inner = inner[match.end() :].strip()

    return _is_read_only_core(inner)


def _is_read_only_core(normalized_sql: str) -> bool:
    if normalized_sql.startswith("explain"):
        return _is_explain_readonly(normalized_sql)

    # MySQL/PostgreSQL metadata commands are always read-only
    if normalized_sql.startswith(("show", "describe", "desc ")):
        return True

    if not normalized_sql.startswith(("select", "with")):
        return False

    if _contains_write_operations(normalized_sql):
        return False

    if SELECT_INTO_RE.search(normalized_sql):
        return False

    if FOR_UPDATE_RE.search(normalized_sql):
        return False

    return True


def is_allowed(sql: str, mode: str) -> bool:
    if _has_multiple_statements(sql):
        return False

    normalized = normalize_sql(_strip_literals_and_comments(sql))
    if not normalized:
        return False

    if mode == "execute":
        return True

    if not normalized.startswith(READ_ONLY_PREFIXES):
        return False

    return _is_read_only_core(normalized)


def is_read_query(sql: str) -> bool:
    normalized = normalize_sql(_strip_literals_and_comments(sql))
    return normalized.startswith(READ_QUERY_PREFIXES)


def has_limit(sql: str) -> bool:
    normalized = normalize_sql(_strip_literals_and_comments(sql))
    return " limit " in normalized


def enforce_limit(sql: str, limit: int) -> str:
    if limit <= 0 or has_limit(sql):
        return sql
    return sql.rstrip(" ;") + f" LIMIT {limit}"
