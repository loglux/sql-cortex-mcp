from typing import Any, Dict, List, Tuple

from app.config import Config
from app.logging import QueryLogEntry, QueryLogger, now_iso
from app.mcp.registry import ToolAnnotations, ToolDef
from app.session_db import SessionDBManager
from app.sql.executor import SQLExecutor
from app.sql.schema import SchemaIntrospector


def build_tools(
    config: Config, logger: QueryLogger, session_mgr: SessionDBManager | None = None
) -> List[Tuple[ToolDef, Any]]:
    # Default executor/introspector for non-session-aware calls
    default_executor = SQLExecutor(config.db_url)
    default_introspector = SchemaIntrospector(config.db_url)

    def _executor(payload: Dict[str, Any]) -> SQLExecutor:
        """Return per-session executor or default."""
        ctx = payload.get("_context") or {}
        sid = ctx.get("session_id")
        if session_mgr and sid:
            engine = session_mgr.get_engine_for_session(sid)
            ex = SQLExecutor.__new__(SQLExecutor)
            ex.engine = engine
            return ex
        return default_executor

    def _introspector(payload: Dict[str, Any]) -> SchemaIntrospector:
        """Return per-session introspector or default."""
        ctx = payload.get("_context") or {}
        sid = ctx.get("session_id")
        if session_mgr and sid:
            engine = session_mgr.get_engine_for_session(sid)
            si = SchemaIntrospector.__new__(SchemaIntrospector)
            si.engine = engine
            return si
        return default_introspector

    def _effective_mode(payload: Dict[str, Any]) -> str:
        """Return per-session mode or default."""
        ctx = payload.get("_context") or {}
        sid = ctx.get("session_id")
        if session_mgr and sid:
            return session_mgr.get_mode(sid, config.mode)
        return config.mode

    def _db_type(payload: Dict[str, Any]) -> str:
        """Return per-session db_type or default."""
        ctx = payload.get("_context") or {}
        sid = ctx.get("session_id")
        if session_mgr and sid:
            return session_mgr.get_db_type(sid)
        return config.db_type

    def sql_query(payload: Dict[str, Any]) -> Dict[str, Any]:
        sql = payload.get("sql", "")
        limit_override = payload.get("limit")
        effective_limit = config.limit_default
        if isinstance(limit_override, int) and limit_override > 0:
            effective_limit = min(limit_override, config.limit_default)
        executor = _executor(payload)
        rows, columns, elapsed_ms, error = executor.execute(
            sql,
            mode="read-only",
            limit_default=effective_limit,
            timeout_ms=config.timeout_ms,
        )
        ok = error is None
        logger.add(
            QueryLogEntry(
                ts=now_iso(),
                tool="sql.query",
                sql=sql,
                ok=ok,
                elapsed_ms=elapsed_ms,
                rows=len(rows),
                error=error,
            )
        )
        response: Dict[str, Any] = {
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
            "elapsed_ms": elapsed_ms,
        }
        if error:
            response["error"] = error
        if config.enable_explanations and not error:
            response["explanation"] = f"Returned {len(rows)} rows."
        return response

    def sql_schema(payload: Dict[str, Any]) -> Dict[str, Any]:
        table = payload.get("table")
        introspector = _introspector(payload)
        schema = introspector.get_schema(table=table)
        return {"schema": schema}

    def sql_explain(payload: Dict[str, Any]) -> Dict[str, Any]:
        sql = payload.get("sql", "")
        plan_sql = f"EXPLAIN {sql}"
        executor = _executor(payload)
        rows, columns, elapsed_ms, error = executor.execute(
            plan_sql,
            mode="read-only",
            limit_default=config.limit_default,
            timeout_ms=config.timeout_ms,
        )
        response: Dict[str, Any] = {
            "plan": rows,
            "columns": columns,
            "elapsed_ms": elapsed_ms,
        }
        if error:
            response["error"] = error
        return response

    def db_design(payload: Dict[str, Any]) -> Dict[str, Any]:
        domain = payload.get("domain", "").strip()
        if not domain:
            return {"error": "Missing domain description"}
        template = {
            "tables": {
                "example_table": {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "name": {"type": "TEXT", "nullable": False},
                    }
                }
            }
        }
        return {
            "note": "Provide a desired schema JSON. Use db.schema.diff to compare with current DB.",
            "desired_schema_template": template,
        }

    def db_schema_diff(payload: Dict[str, Any]) -> Dict[str, Any]:
        desired = payload.get("desired_schema")
        if not isinstance(desired, dict):
            return {"error": "desired_schema must be an object"}
        introspector = _introspector(payload)
        current = introspector.get_schema_simple()
        desired_tables = desired.get("tables", {})
        current_tables = current.get("tables", {})

        missing_tables = [t for t in desired_tables.keys() if t not in current_tables]
        extra_tables = [t for t in current_tables.keys() if t not in desired_tables]

        missing_columns = {}
        extra_columns = {}
        type_mismatches = {}
        nullable_mismatches = {}
        missing_indexes = {}
        extra_indexes = {}
        for table, spec in desired_tables.items():
            if table not in current_tables:
                continue
            desired_cols = spec.get("columns", {})
            current_cols = current_tables[table].get("columns", {})
            missing = [c for c in desired_cols.keys() if c not in current_cols]
            extra = [c for c in current_cols.keys() if c not in desired_cols]
            if missing:
                missing_columns[table] = missing
            if extra:
                extra_columns[table] = extra

            for col_name, col_spec in desired_cols.items():
                if col_name not in current_cols:
                    continue
                desired_type = str(col_spec.get("type", "")).upper()
                current_type = str(current_cols[col_name].get("type", "")).upper()
                if desired_type and current_type and desired_type != current_type:
                    type_mismatches.setdefault(table, []).append(
                        {"column": col_name, "desired": desired_type, "current": current_type}
                    )
                desired_nullable = col_spec.get("nullable")
                current_nullable = current_cols[col_name].get("nullable")
                if desired_nullable is not None and desired_nullable != current_nullable:
                    nullable_mismatches.setdefault(table, []).append(
                        {
                            "column": col_name,
                            "desired": desired_nullable,
                            "current": current_nullable,
                        }
                    )

            desired_indexes = [_normalize_index(i) for i in spec.get("indexes", [])]
            current_indexes = [
                _normalize_index(i) for i in current_tables[table].get("indexes", [])
            ]
            desired_norm = {_index_key(i) for i in desired_indexes}
            current_norm = {_index_key(i) for i in current_indexes}
            missing_idx = [i for i in desired_indexes if _index_key(i) not in current_norm]
            extra_idx = [i for i in current_indexes if _index_key(i) not in desired_norm]
            if missing_idx:
                missing_indexes[table] = missing_idx
            if extra_idx:
                extra_indexes[table] = extra_idx

        return {
            "missing_tables": missing_tables,
            "extra_tables": extra_tables,
            "missing_columns": missing_columns,
            "extra_columns": extra_columns,
            "type_mismatches": type_mismatches,
            "nullable_mismatches": nullable_mismatches,
            "missing_indexes": missing_indexes,
            "extra_indexes": extra_indexes,
        }

    def _normalize_index(index: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": index.get("name"),
            "columns": index.get("columns") or index.get("column_names") or [],
            "unique": bool(index.get("unique", False)),
        }

    def _index_key(index: Dict[str, Any]) -> tuple:
        name = index.get("name") or ""
        cols = tuple(index.get("columns") or [])
        unique = bool(index.get("unique", False))
        return (name, cols, unique)

    def _qi(identifier: str) -> str:
        """Quote a SQL identifier to prevent injection."""
        sanitized = identifier.replace('"', '""')
        return f'"{sanitized}"'

    def _create_index_sql(dialect: str, name: str, table: str, cols: str, unique: str) -> str:
        return f"CREATE {unique}INDEX {_qi(name)} ON {_qi(table)} ({cols});"

    def _drop_index_sql(dialect: str, name: str, table: str) -> str:
        if dialect == "mysql":
            return f"DROP INDEX {_qi(name)} ON {_qi(table)};"
        return f"DROP INDEX {_qi(name)};"

    def _alter_type_sql(
        dialect: str, table: str, column: str, desired: str
    ) -> tuple[str | None, str | None]:
        t, c = _qi(table), _qi(column)
        if dialect == "postgresql":
            return (f"ALTER TABLE {t} ALTER COLUMN {c} TYPE {desired};", None)
        if dialect == "mysql":
            return (f"ALTER TABLE {t} MODIFY COLUMN {c} {desired};", None)
        if dialect == "sqlite":
            return (None, f"SQLite does not support ALTER COLUMN TYPE for {table}.{column}")
        return (f"ALTER TABLE {t} ALTER COLUMN {c} TYPE {desired};", None)

    def _alter_nullable_sql(
        dialect: str, table: str, column: str, desired_nullable: bool
    ) -> tuple[str | None, str | None]:
        t, c = _qi(table), _qi(column)
        if dialect == "postgresql":
            if desired_nullable:
                return (f"ALTER TABLE {t} ALTER COLUMN {c} DROP NOT NULL;", None)
            return (f"ALTER TABLE {t} ALTER COLUMN {c} SET NOT NULL;", None)
        if dialect == "mysql":
            return (None, f"MySQL requires full column type to change NULL for {table}.{column}")
        if dialect == "sqlite":
            return (None, f"SQLite does not support ALTER COLUMN NULL for {table}.{column}")
        return (None, f"Nullable change not supported for {dialect}")

    def _drop_column_sql(dialect: str, table: str, column: str) -> tuple[str | None, str | None]:
        if dialect in {"postgresql", "mysql"}:
            return (f"ALTER TABLE {_qi(table)} DROP COLUMN {_qi(column)};", None)
        if dialect == "sqlite":
            return (None, f"SQLite does not support DROP COLUMN for {table}.{column}")
        return (None, f"DROP COLUMN not supported for {dialect}")

    def db_apply(payload: Dict[str, Any]) -> Dict[str, Any]:
        sql = payload.get("sql", "")
        if not sql.strip():
            return {"error": "Missing sql"}
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        if len(statements) != 1:
            return {"error": "Only a single SQL statement is allowed"}
        executor = _executor(payload)
        mode = _effective_mode(payload)
        _, _, elapsed_ms, error = executor.execute(
            statements[0],
            mode=mode,
            limit_default=config.limit_default,
            timeout_ms=config.timeout_ms,
        )
        if error:
            return {"error": error}
        return {"ok": True, "elapsed_ms": elapsed_ms}

    def db_migrate(payload: Dict[str, Any]) -> Dict[str, Any]:
        sql = payload.get("sql", "")
        dry_run = bool(payload.get("dry_run", False))
        if not sql.strip():
            return {"error": "Missing sql"}
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        if not statements:
            return {"error": "No SQL statements found"}
        mode = _effective_mode(payload)
        if mode != "execute":
            return {"error": "Migration requires MODE=execute"}
        executor = _executor(payload)

        results = []
        for stmt in statements:
            if dry_run:
                results.append({"sql": stmt, "ok": True, "dry_run": True})
                continue
            _, _, elapsed_ms, error = executor.execute(
                stmt,
                mode=mode,
                limit_default=config.limit_default,
                timeout_ms=config.timeout_ms,
            )
            if error:
                results.append({"sql": stmt, "ok": False, "error": error})
                break
            results.append({"sql": stmt, "ok": True, "elapsed_ms": elapsed_ms})

        return {
            "count": len(results),
            "results": results,
            "dry_run": dry_run,
        }

    def db_migrate_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
        desired = payload.get("desired_schema")
        destructive = bool(payload.get("destructive", False))
        if not isinstance(desired, dict):
            return {"error": "desired_schema must be an object"}
        if destructive and not config.allow_destructive:
            return {"error": "Destructive operations are disabled by config"}

        diff = db_schema_diff({"desired_schema": desired, "_context": payload.get("_context")})
        if diff.get("error"):
            return diff

        statements: List[str] = []
        warnings: List[str] = []
        desired_tables = desired.get("tables", {})
        executor = _executor(payload)
        dialect = executor.engine.dialect.name

        for table in diff.get("missing_tables", []):
            spec = desired_tables.get(table, {})
            columns = spec.get("columns", {})
            col_defs = []
            for name, c in columns.items():
                col_type = c.get("type", "TEXT")
                nullable = c.get("nullable", True)
                col_defs.append(f"{_qi(name)} {col_type}{'' if nullable else ' NOT NULL'}")
            if col_defs:
                statements.append(f"CREATE TABLE {_qi(table)} ({', '.join(col_defs)});")

            for idx in spec.get("indexes", []):
                idx_norm = _normalize_index(idx)
                idx_name = idx_norm.get("name") or f"idx_{table}_{'_'.join(idx_norm['columns'])}"
                unique = "UNIQUE " if idx_norm.get("unique") else ""
                cols = ", ".join(_qi(c) for c in idx_norm.get("columns", []))
                if cols:
                    statements.append(_create_index_sql(dialect, idx_name, table, cols, unique))

        for table, cols in diff.get("missing_columns", {}).items():
            for col in cols:
                spec = desired_tables.get(table, {}).get("columns", {}).get(col, {})
                col_type = spec.get("type", "TEXT")
                nullable = spec.get("nullable", True)
                not_null = "" if nullable else " NOT NULL"
                stmt = f"ALTER TABLE {_qi(table)} ADD COLUMN {_qi(col)} {col_type}{not_null};"
                statements.append(stmt)

        for table, cols in diff.get("type_mismatches", {}).items():
            for item in cols:
                stmt, warn = _alter_type_sql(dialect, table, item["column"], item["desired"])
                if stmt:
                    statements.append(stmt)
                if warn:
                    warnings.append(warn)

        for table, cols in diff.get("nullable_mismatches", {}).items():
            for item in cols:
                stmt, warn = _alter_nullable_sql(dialect, table, item["column"], item["desired"])
                if stmt:
                    statements.append(stmt)
                if warn:
                    warnings.append(warn)

        for table, idxs in diff.get("missing_indexes", {}).items():
            for idx in idxs:
                idx_norm = _normalize_index(idx)
                idx_name = idx_norm.get("name") or f"idx_{table}_{'_'.join(idx_norm['columns'])}"
                unique = "UNIQUE " if idx_norm.get("unique") else ""
                cols = ", ".join(_qi(c) for c in idx_norm.get("columns", []))
                if cols:
                    statements.append(_create_index_sql(dialect, idx_name, table, cols, unique))

        if destructive:
            for table in diff.get("extra_tables", []):
                statements.append(f"DROP TABLE {_qi(table)};")
            for table, cols in diff.get("extra_columns", {}).items():
                for col in cols:
                    stmt, warn = _drop_column_sql(dialect, table, col)
                    if stmt:
                        statements.append(stmt)
                    if warn:
                        warnings.append(warn)
            for table, idxs in diff.get("extra_indexes", {}).items():
                for idx in idxs:
                    name = _normalize_index(idx).get("name")
                    if name:
                        statements.append(_drop_index_sql(dialect, name, table))

        return {
            "count": len(statements),
            "statements": statements,
            "destructive": destructive,
            "dialect": dialect,
            "warnings": warnings,
        }

    def db_migrate_plan_apply(payload: Dict[str, Any]) -> Dict[str, Any]:
        desired = payload.get("desired_schema")
        destructive = bool(payload.get("destructive", False))
        dry_run = bool(payload.get("dry_run", False))
        plan = db_migrate_plan(
            {
                "desired_schema": desired,
                "destructive": destructive,
                "_context": payload.get("_context"),
            }
        )
        if plan.get("error"):
            return plan
        sql = ";\n".join(plan.get("statements", []))
        if not sql.strip():
            return {"error": "Plan produced no statements"}
        migrate_result = db_migrate(
            {"sql": sql, "dry_run": dry_run, "_context": payload.get("_context")}
        )
        migrate_result["plan"] = plan
        return migrate_result

    # ── db.list / db.use ─────────────────────────────────────────────────────

    def db_list(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not session_mgr:
            return {"connections": [], "note": "Multi-database not enabled"}
        connections = session_mgr.list_connections()
        ctx = payload.get("_context") or {}
        sid = ctx.get("session_id")
        # Mark which one this session is using
        session_conn = session_mgr.get_session_connection(sid)
        session_conn_id = session_conn["id"] if session_conn else None
        for c in connections:
            c["current"] = c["id"] == session_conn_id if session_conn_id else c["is_active"]
        return {"connections": connections}

    def db_use(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not session_mgr:
            return {"error": "Multi-database not enabled"}
        ctx = payload.get("_context") or {}
        sid = ctx.get("session_id")
        if not sid:
            return {"error": "No MCP session — db.use requires a session"}
        connection_id = payload.get("connection_id")
        connection_name = payload.get("name")
        if connection_id is None and not connection_name:
            return {"error": "Provide connection_id or name"}
        connections = session_mgr.list_connections()
        target = None
        if connection_id is not None:
            target = next((c for c in connections if c["id"] == connection_id), None)
        elif connection_name:
            target = next((c for c in connections if c["name"] == connection_name), None)
        if not target:
            return {"error": f"Connection not found: {connection_id or connection_name}"}
        session_mgr.set_session_db(sid, target["id"])
        return {
            "ok": True,
            "active": {
                "id": target["id"],
                "name": target["name"],
                "db_type": target["db_type"],
                "mode": target["mode"],
                "host": target["host"],
            },
        }

    tools: list[tuple[ToolDef, Any]] = [
        (
            ToolDef(
                name="sql.query",
                title="SQL Query",
                description=(
                    f"Execute a read-only SQL query against {config.db_type}"
                    f"{' ' + config.db_version if config.db_version else ''}. "
                    f"Use {config.db_type}-compatible syntax. "
                    "SELECT, WITH, EXPLAIN, SHOW, and DESCRIBE statements are allowed. "
                    "For INSERT, UPDATE, DELETE, or DDL statements use db.apply."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["sql"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array", "items": {"type": "object"}},
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "row_count": {"type": "integer"},
                        "elapsed_ms": {"type": "integer"},
                        "error": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(
                    read_only_hint=True,
                    idempotent_hint=True,
                ),
            ),
            sql_query,
        ),
        (
            ToolDef(
                name="sql.schema",
                title="SQL Schema",
                description=(
                    "Inspect database schema: tables, columns, types, indexes, "
                    "and foreign keys. Read-only introspection, no data is modified."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"table": {"type": "string"}},
                },
                output_schema={
                    "type": "object",
                    "properties": {"schema": {"type": "object"}},
                },
                annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
            ),
            sql_schema,
        ),
        (
            ToolDef(
                name="sql.explain",
                title="SQL Explain",
                description=(
                    "Return the EXPLAIN plan for a SQL query. "
                    "Read-only, does not execute the query."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "plan": {"type": "array", "items": {"type": "object"}},
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "elapsed_ms": {"type": "integer"},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
            ),
            sql_explain,
        ),
        (
            ToolDef(
                name="db.list",
                title="List Databases",
                description=(
                    "List all registered database connections. "
                    "Shows name, type, host, mode, and which one is "
                    "currently active for this session."
                ),
                input_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "properties": {
                        "connections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "integer"},
                                    "name": {"type": "string"},
                                    "db_type": {"type": "string"},
                                    "version": {"type": "string"},
                                    "mode": {"type": "string"},
                                    "host": {"type": "string"},
                                    "is_active": {"type": "boolean"},
                                    "current": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
                annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
            ),
            db_list,
        ),
        (
            ToolDef(
                name="db.use",
                title="Switch Database",
                description=(
                    "Switch the active database for this MCP session. "
                    "Provide either connection_id or name from db.list. "
                    "Only affects this session — other sessions keep their own database."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "connection_id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "active": {"type": "object"},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True),
            ),
            db_use,
        ),
        (
            ToolDef(
                name="db.design",
                title="DB Design",
                description=(
                    "Return a desired schema JSON template for design workflows. "
                    "Read-only, provides a starting template for db.schema.diff."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"domain": {"type": "string"}},
                    "required": ["domain"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "note": {"type": "string"},
                        "desired_schema_template": {"type": "object"},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
            ),
            db_design,
        ),
        (
            ToolDef(
                name="db.schema.diff",
                title="DB Schema Diff",
                description=(
                    "Compare desired schema JSON with current database schema. "
                    "Read-only diff showing missing/extra tables, columns, and indexes."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"desired_schema": {"type": "object"}},
                    "required": ["desired_schema"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "missing_tables": {"type": "array", "items": {"type": "string"}},
                        "extra_tables": {"type": "array", "items": {"type": "string"}},
                        "missing_columns": {"type": "object"},
                        "extra_columns": {"type": "object"},
                        "type_mismatches": {"type": "object"},
                        "nullable_mismatches": {"type": "object"},
                        "missing_indexes": {"type": "object"},
                        "extra_indexes": {"type": "object"},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
            ),
            db_schema_diff,
        ),
        (
            ToolDef(
                name="db.migrate.plan",
                title="DB Migrate Plan",
                description=(
                    "Generate SQL migration statements from desired schema diff. "
                    "Read-only planning step, does not execute any statements."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "desired_schema": {"type": "object"},
                        "destructive": {"type": "boolean"},
                    },
                    "required": ["desired_schema"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "statements": {"type": "array", "items": {"type": "string"}},
                        "destructive": {"type": "boolean"},
                        "dialect": {"type": "string"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
            ),
            db_migrate_plan,
        ),
        (
            ToolDef(
                name="db.apply",
                title="DB Apply",
                description=(
                    "Execute a single mutating SQL statement that changes database state. "
                    "Supports INSERT, UPDATE, DELETE, and DDL (CREATE, ALTER, DROP). "
                    "Requires server MODE=execute. For read-only queries use sql.query."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "elapsed_ms": {"type": "integer"},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(destructive_hint=True),
            ),
            db_apply,
        ),
        (
            ToolDef(
                name="db.migrate",
                title="DB Migrate",
                description=(
                    "Apply a batch of mutating SQL statements with optional dry-run. "
                    "Requires server MODE=execute."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "dry_run": {"type": "boolean"},
                    },
                    "required": ["sql"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "results": {"type": "array", "items": {"type": "object"}},
                        "dry_run": {"type": "boolean"},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(destructive_hint=True),
            ),
            db_migrate,
        ),
        (
            ToolDef(
                name="db.migrate.plan_apply",
                title="DB Migrate Plan Apply",
                description=(
                    "Generate a migration plan from desired schema and apply it. "
                    "Combines db.migrate.plan + db.migrate. Requires server MODE=execute."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "desired_schema": {"type": "object"},
                        "destructive": {"type": "boolean"},
                        "dry_run": {"type": "boolean"},
                    },
                    "required": ["desired_schema"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "results": {"type": "array", "items": {"type": "object"}},
                        "dry_run": {"type": "boolean"},
                        "plan": {"type": "object"},
                        "error": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(destructive_hint=True),
            ),
            db_migrate_plan_apply,
        ),
    ]

    return tools
