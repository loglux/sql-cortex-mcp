from typing import Any, Dict, List


class LLMProvider:
    async def generate(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError
