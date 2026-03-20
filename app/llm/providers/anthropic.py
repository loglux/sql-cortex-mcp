"""Native Anthropic Messages API provider.

Uses https://api.anthropic.com/v1/messages directly.
Required for full Claude features: extended thinking, prompt caching, PDF, etc.
"""

from typing import Any, Dict, List

import httpx

from app.llm.base import LLMProvider

ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str | None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    async def generate(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("LLM_API_KEY is required for Anthropic provider")

        # Separate system message from conversation
        system_content = ""
        conversation: List[Dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                conversation.append({"role": msg["role"], "content": msg["content"]})

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": conversation,
        }
        if system_content:
            payload["system"] = system_content

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

        url = f"{self.base_url}/v1/messages"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        text = ""
        try:
            text = data["content"][0]["text"] or ""
        except (KeyError, IndexError):
            pass
        return {"text": text, "raw": data}
