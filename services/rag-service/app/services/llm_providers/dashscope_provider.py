"""DashScope qwen LLM Provider（V5，design D8，复用 query_rewrite 同款调用/鉴权）。"""
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


class DashScopeProvider(LLMProvider):
    """阿里云 DashScope qwen 回答实现（text-generation 原生格式，与 query_rewrite 一致）。

    连续 3 次失败写入 Redis `rag:llm:degraded`（TTL 60s），窗口内短路；
    Redis 探测失败按未熔断处理（不阻断正常链路）。
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
            logger.error("llm_circuit_break_open", failures=self._failure_count, error=str(error))

    async def generate(
        self, context: list[dict], query: str, timeout: float
    ) -> LLMAnswer:
        if await self._is_degraded():
            raise RuntimeError("LLM service is in circuit-break state")
        if not settings.DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY not configured")

        system, user = build_answer_prompt(context, query)
        headers = {
            "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.LLM_MODEL,
            "input": {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            },
        }

        client = await self._get_client()
        for attempt in range(3):
            try:
                resp = await client.post(
                    settings.LLM_DASHSCOPE_ENDPOINT,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                text = resp.json()["output"]["text"]
                await self._on_success()
                return LLMAnswer(
                    text=text,
                    citations=sorted({c["doc_id"] for c in context if c.get("doc_id")}),
                )
            except (httpx.HTTPError, KeyError, ValueError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
