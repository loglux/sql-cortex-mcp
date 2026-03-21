import asyncio
import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

# Retryable HTTP status codes (server errors + rate limiting)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

LLM_MAX_RETRIES = 2
LLM_RETRY_BASE_DELAY = 1.0  # seconds


class LLMProvider:
    async def generate(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1 + LLM_MAX_RETRIES):
            try:
                return await self._generate(messages)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    raise
                if attempt < LLM_MAX_RETRIES:
                    delay = LLM_RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "LLM request failed (%s), retrying in %.1fs (attempt %d/%d)",
                        exc.response.status_code,
                        delay,
                        attempt + 1,
                        LLM_MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < LLM_MAX_RETRIES:
                    delay = LLM_RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "LLM request failed (%s), retrying in %.1fs (attempt %d/%d)",
                        type(exc).__name__,
                        delay,
                        attempt + 1,
                        LLM_MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def _generate(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError
