from typing import Any, Dict, List

import httpx

from app.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, base_url: str | None, timeout: float = 60) -> None:
        self.model = model
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.timeout = timeout

    async def _generate(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        text = data.get("message", {}).get("content", "")
        return {"text": text, "raw": data}
