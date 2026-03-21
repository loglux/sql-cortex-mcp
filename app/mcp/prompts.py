from typing import Any, Dict

from app.config import Config


class PromptRegistry:
    def __init__(self, config: Config) -> None:
        self.config = config

    def list_prompts(self) -> Dict[str, Any]:
        return {
            "prompts": [
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

    def get_prompt(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
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
