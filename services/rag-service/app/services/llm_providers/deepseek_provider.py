"""DeepSeek OpenAI 兼容 LLM Provider（V5）。"""
import asyncio

import httpx
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.llm_providers.base import LLMAnswer, LLMProvider, build_answer_prompt
from common.config.settings import settings

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:llm:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60


class DeepSeekProvider(LLMProvider):
    """DeepSeek Chat Completions（OpenAI 兼容 /v1/chat/completions）。

    API Key 读取 settings.deepseek_api_key_resolved（兼容 DEEPSEEK_API_KEY / DeepSeek_API_KEY）。
    """

    def __init__(self, redis: Redis | None = None):
        self._redis = redis
        self._client: httpx.AsyncClient | None = None
        self._failure_count = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def _is_degraded(self) -> bool:
        if self._redis is None:
            return False
        try:
            return await self._redis.exists(_DEGRADED_KEY) > 0
        except (RedisError, OSError) as e:
            logger.warning("llm_degraded_probe_failed", error=str(e))
            return False

    async def _on_success(self) -> None:
        self._failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        self._failure_count += 1
        if self._failure_count >= _FAILURE_THRESHOLD:
            if self._redis is not None:
                await self._redis.setex(_DEGRADED_KEY, _CIRCUIT_TTL, "1")
            logger.error(
                "llm_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
            )

    async def generate(
        self, context: list[dict], query: str, timeout: float
    ) -> LLMAnswer:
        api_key = settings.deepseek_api_key_resolved
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured")
        if await self._is_degraded():
            raise RuntimeError("LLM service is in circuit-break state")

        system, user = build_answer_prompt(context, query)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
        }
        url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
        client = await self._get_client()
        for attempt in range(3):
            try:
                resp = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                await self._on_success()
                return LLMAnswer(
                    text=text,
                    citations=sorted({c["doc_id"] for c in context if c.get("doc_id")}),
                )
            except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
