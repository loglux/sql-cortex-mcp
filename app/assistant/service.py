import json
import re
from typing import Any, Dict, List

from app.config import Config
from app.llm.base import LLMProvider
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.chat_completions import ChatCompletionsProvider
from app.llm.providers.ollama import OllamaProvider
from app.mcp.registry import ToolRegistry

SYSTEM_PROMPT = """You are an expert SQL assistant connected to a {db_type} database.

The current database schema is:
{schema}

RULES:
1. When the user asks about data — immediately write and execute the SQL query.
2. Always respond in valid JSON only, no markdown, no extra text.
3. Use this exact format:

{{
  "thought": "brief reasoning",
  "sql": "the SQL query you want to execute, or null if no query needed",
  "explanation": "plain English explanation of the result"
}}

If you cannot answer with SQL, set "sql" to null and explain in "explanation".
Return SQL as plain text — no escaped quotes, no unicode escapes.
"""


class AssistantService:
    def __init__(self, config: Config, registry: ToolRegistry) -> None:
        self.config = config
        self.registry = registry
        self.provider = _build_provider(config)

    async def chat(
        self,
        user_message: str,
        history: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        schema_result = self.registry.call("sql.schema", {})
        schema_json = json.dumps(schema_result.get("schema", {}), indent=2)

        db_type = _detect_db_type(self.config.db_url)
        system = SYSTEM_PROMPT.format(db_type=db_type, schema=schema_json)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        llm_result = await self.provider.generate(messages)
        raw_text = llm_result.get("text", "")

        parsed = _parse_llm_response(raw_text)
        sql = parsed.get("sql")
        query_result: Dict[str, Any] | None = None

        if sql:
            query_result = self.registry.call(
                "sql.query", {"sql": sql, "limit": self.config.limit_default}
            )
            if query_result.get("error"):
                parsed["error"] = query_result["error"]

        return {
            "thought": parsed.get("thought", ""),
            "sql": sql,
            "explanation": parsed.get("explanation", ""),
            "error": parsed.get("error"),
            "query_result": query_result,
            "raw": raw_text,
        }


def _parse_llm_response(text: str) -> Dict[str, Any]:
    text = text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON object from text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {"thought": "", "sql": None, "explanation": text}


def _detect_db_type(db_url: str) -> str:
    if "postgresql" in db_url or "postgres" in db_url:
        return "PostgreSQL"
    if "mysql" in db_url:
        return "MySQL"
    if "sqlite" in db_url:
        return "SQLite"
    return "SQL"


"""
Supported LLM_PROVIDER values and their base URLs:

  openai      → https://api.openai.com
  anthropic   → https://api.anthropic.com       (native Messages API)
  deepseek    → https://api.deepseek.com
  ollama      → http://localhost:11434
  gemini      → https://generativelanguage.googleapis.com/v1beta/openai
  groq        → https://api.groq.com/openai
  mistral     → https://api.mistral.ai

For any OpenAI-compatible provider not listed, set LLM_BASE_URL manually.

Suggested models per provider (as of 2026):
  openai:    gpt-5.4-mini, gpt-5.4, gpt-5.4-nano, gpt-5.2, gpt-5.1, o4-mini, o3
  anthropic: claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5
  deepseek:  deepseek-chat, deepseek-reasoner
  ollama:    llama3.3, qwen2.5-coder, deepseek-r1, gemma3, phi4
  gemini:    gemini-2.5-pro, gemini-2.5-flash, gemini-2.0-flash, gemini-2.0-flash-lite
  groq:      llama-3.3-70b-versatile, llama-3.1-8b-instant, gemma2-9b-it
  mistral:   mistral-large-latest, mistral-small-latest, codestral-latest
"""

_PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "deepseek": "https://api.deepseek.com",
    "ollama": "http://localhost:11434",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai",
    "mistral": "https://api.mistral.ai",
}


def _build_provider(config: Config) -> LLMProvider:
    provider = config.llm_provider.lower()
    base_url = config.llm_base_url or _PROVIDER_BASE_URLS.get(provider)
    timeout = config.llm_timeout_ms / 1000

    if provider == "anthropic":
        return AnthropicProvider(
            api_key=config.llm_api_key,
            model=config.llm_model,
            base_url=base_url,
            timeout=timeout,
        )

    if provider == "ollama":
        return OllamaProvider(
            model=config.llm_model,
            base_url=base_url,
            timeout=timeout,
        )

    # All others: OpenAI-compatible /v1/chat/completions
    return ChatCompletionsProvider(
        api_key=config.llm_api_key,
        model=config.llm_model,
        base_url=base_url,
        timeout=timeout,
    )
