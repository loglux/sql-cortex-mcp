from typing import Any, Dict

from app.config import Config

_ASSISTANT_ROLE = (
    "You are a database assistant connected to a live SQL database via MCP "
    "(Model Context Protocol).\n\n"
    "Rules:\n"
    "1. ALWAYS call sql.schema first at the start of each conversation to get "
    "the current schema. Never assume or cache table names from previous messages.\n"
    "2. Read the sql.query tool description — it tells you the database engine "
    "(SQLite, MySQL, PostgreSQL). Use the correct SQL dialect.\n"
    "3. For read queries use sql.query. For writes use db.apply. Never mix them.\n"
    "4. If a query fails with a syntax error, check the dialect and retry "
    "with correct syntax.\n"
    "5. Prefer sql.schema over SHOW TABLES / information_schema for discovering tables.\n"
    "6. When presenting results, format them as clean tables. Keep explanations concise."
)


class PromptRegistry:
    def __init__(self, config: Config) -> None:
        self.config = config

    def list_prompts(self) -> Dict[str, Any]:
        return {
            "prompts": [
                {
                    "name": "sql.assistant.role",
                    "description": (
                        "System prompt for an AI assistant working with this database. "
                        "Provides rules for correct tool usage, dialect detection, "
                        "and schema discovery."
                    ),
                    "arguments": [],
                },
                {
                    "name": "sql.query.plan",
                    "description": "Generate a safe SQL query plan before execution.",
                    "arguments": [
                        {
                            "name": "question",
                            "description": "User's request in natural language.",
                            "required": True,
                        }
                    ],
                },
                {
                    "name": "db.design.schema",
                    "description": "Propose a SQL schema from a domain description.",
                    "arguments": [
                        {
                            "name": "domain",
                            "description": "Problem domain and entities.",
                            "required": True,
                        },
                        {
                            "name": "db",
                            "description": "Target database engine (postgres, mysql, sqlite).",
                            "required": False,
                        },
                    ],
                },
            ]
        }

    def get_prompt(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any] | None:
        if name == "sql.assistant.role":
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": _ASSISTANT_ROLE},
                    },
                ]
            }

        if name == "sql.query.plan":
            question = arguments.get("question", "")
            content = (
                "You are an expert SQL assistant. Provide a safe, minimal SQL plan. "
                "Prefer SELECT/CTE, and keep the query limited.\n\n"
                f"User request: {question}"
            )
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": content},
                    },
                ]
            }

        if name == "db.design.schema":
            domain = arguments.get("domain", "")
            db = arguments.get("db", "postgres")
            content = (
                "Design a relational schema with keys, indexes, and constraints. "
                "Return SQL DDL suitable for the target DB.\n\n"
                f"Target DB: {db}\n"
                f"Domain: {domain}"
            )
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": content},
                    },
                ]
            }

        return None
