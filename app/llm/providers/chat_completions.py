"""Universal provider for any OpenAI-compatible /v1/chat/completions API.

Works with: OpenAI, DeepSeek, Ollama, Google Gemini (via compat endpoint),
and any other provider that implements the OpenAI chat completions format.
"""

from typing import Any, Dict, List

import httpx

from app.llm.base import LLMProvider


class ChatCompletionsProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str | None, timeout: float = 60) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com").rstrip("/")
        self.timeout = timeout

    async def _generate(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        text = ""
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            pass
        return {"text": text, "raw": data}
