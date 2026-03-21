<p align="center">
  <img src="app/web/static/favicon.svg" width="80" height="80" alt="SQL Cortex MCP">
</p>

<h1 align="center">SQL Cortex MCP</h1>

<p align="center">
  MCP server that gives AI agents (Claude, OpenAI, Gemini, etc.) direct access to SQL databases — with policy enforcement, schema tools, and an admin UI.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/MCP-2025--11--25-green" alt="MCP Protocol">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="MIT License">
</p>

---

## What is this?

SQL Cortex MCP is a [Model Context Protocol](https://modelcontextprotocol.io) server that exposes SQL databases as tools for AI agents. It sits between your LLM and your database, providing:

- **9 MCP tools** — query, schema introspection, explain plans, migrations, and more
- **Policy enforcement** — read-only mode, query allowlists, row limits, timeouts
- **Admin UI** — web dashboard with query sandbox, schema browser, chat assistant, settings
- **Multi-provider LLM** — OpenAI, Anthropic, DeepSeek, Ollama, Gemini, Groq, Mistral
- **Persistent settings** — encrypted API keys, runtime config changes survive restarts

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/loglux/sql-cortex-mcp.git
cd sql-cortex-mcp
docker compose up -d
```

Open [http://localhost:8123](http://localhost:8123) for the admin UI.

### Local

```bash
git clone https://github.com/loglux/sql-cortex-mcp.git
cd sql-cortex-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Connect your AI agent

Point any MCP-compatible client at the server:

```
MCP endpoint:  http://localhost:8123/mcp
Protocol:      HTTP + JSON-RPC
SSE endpoint:  http://localhost:8123/mcp (GET for Server-Sent Events)
```

## MCP Tools

| Tool | Description |
|---|---|
| `sql.query` | Execute read-only SQL queries (SELECT, WITH, EXPLAIN) |
| `sql.schema` | Introspect tables, columns, types, and indexes |
| `sql.explain` | Get EXPLAIN plan for a query |
| `db.design` | Generate a desired schema template |
| `db.schema.diff` | Compare desired schema against current database |
| `db.migrate.plan` | Generate migration SQL from a schema diff |
| `db.apply` | Execute a single mutating statement (INSERT, UPDATE, DELETE, DDL) |
| `db.migrate` | Apply a batch of SQL statements |
| `db.migrate.plan_apply` | Plan and apply migration in one call |

## MCP Resources

| Resource | Description |
|---|---|
| `resource://schema` | Current database schema (tables, columns, indexes, foreign keys) |
| `resource://config` | Non-secret runtime configuration summary |

## MCP Prompts

| Prompt | Description |
|---|---|
| `sql.query.plan` | Generate a safe SQL query plan from a natural language question |
| `db.design.schema` | Propose a SQL schema from a domain description |

## Admin UI

The built-in web UI provides:

- **Dashboard** — server status, quick actions
- **Query** — SQL sandbox with syntax highlighting and result tables
- **Schema** — browse tables, columns, indexes
- **Chat** — natural language to SQL via LLM (multiple sessions, persistent history)
- **Logs** — query audit trail
- **Settings** — configure DB connection, LLM providers, runtime parameters

## Configuration

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `DB_URL` | `sqlite:///./data/dev.db` | SQLAlchemy connection URL |
| `MODE` | `read-only` | `read-only` or `execute` |
| `LIMIT_DEFAULT` | `100` | Max rows per query (0 = unlimited) |
| `TIMEOUT_MS` | `5000` | Query timeout in milliseconds |
| `ENABLE_UI` | `true` | Enable the admin web UI |
| `ENABLE_EXPLANATIONS` | `true` | Include query explanations in results |
| `ALLOWED_ORIGINS` | `localhost` | CORS origins (comma-separated) |
| `ALLOW_DESTRUCTIVE` | `false` | Allow DROP/destructive operations |
| `LLM_PROVIDER` | `openai` | LLM provider name |
| `LLM_API_KEY` | — | API key for the LLM provider |
| `LLM_MODEL` | `gpt-5.4-mini` | Model name |
| `LLM_BASE_URL` | — | Custom base URL for the provider |

Settings changed via the admin UI override environment variables at runtime and persist across restarts.

### Supported LLM providers

| Provider | Base URL | Example models |
|---|---|---|
| `openai` | `api.openai.com` | gpt-5.4-mini, gpt-5.4, o4-mini, o3 |
| `anthropic` | `api.anthropic.com` | claude-sonnet-4-6, claude-opus-4-6 |
| `deepseek` | `api.deepseek.com` | deepseek-chat, deepseek-reasoner |
| `ollama` | `localhost:11434` | llama3.3, qwen2.5-coder, phi4 |
| `gemini` | `generativelanguage.googleapis.com` | gemini-2.5-pro, gemini-2.5-flash |
| `groq` | `api.groq.com` | llama-3.3-70b-versatile |
| `mistral` | `api.mistral.ai` | mistral-large-latest |

### Supported databases

- **SQLite** — built-in, no extra dependencies
- **PostgreSQL** — via `psycopg2` (included in requirements)

## Development

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Lint
ruff check .

# Format
black --check .

# Test
pytest

# Test with coverage
pytest --cov=app --cov-report=term-missing
```

## Project Structure

```
app/
  main.py            — FastAPI app, MCP JSON-RPC router
  config.py          — Config from env vars + settings DB
  settings_db.py     — SQLite persistence (providers, settings, chat)
  mcp/               — MCP protocol: tools, resources, prompts
  sql/               — SQL executor, policy engine, schema introspection
  llm/               — LLM providers (OpenAI-compat, Anthropic, Ollama)
  assistant/         — Chat assistant service
  web/               — Admin UI (FastAPI + Jinja2 + HTMX)
tests/               — pytest test suite
```

## License

MIT
