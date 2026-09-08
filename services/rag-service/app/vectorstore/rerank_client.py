"""Rerank 客户端：HTTP 调用 rerank-service:8101，含重试与熔断逻辑。"""
import asyncio

import httpx
import structlog
from redis.asyncio import Redis

from app.settings import settings

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:rerank:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60


class RerankClient:
    def __init__(
        self,
        endpoint: str | None = None,
        redis: Redis | None = None,
    ):
        self.endpoint = endpoint or settings.RERANK_SERVICE_URL
        self._redis = redis
        self._client: httpx.AsyncClient | None = None
        self._failure_count = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def is_degraded(self) -> bool:
        if self._redis is None:
            return False
        return await self._redis.exists(_DEGRADED_KEY) > 0

    async def _on_success(self) -> None:
        self._failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        self._failure_count += 1
        if self._failure_count >= _FAILURE_THRESHOLD:
            await self._set_degraded()
            logger.error(
                "rerank_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
            )

    async def _set_degraded(self, ttl: int = _CIRCUIT_TTL) -> None:
        if self._redis is not None:
            await self._redis.setex(_DEGRADED_KEY, ttl, "1")

    async def rerank(
        self, query: str, candidates: list[dict]
    ) -> list[dict]:
        """调用 rerank-service。

        Args:
            query: 查询文本
            candidates: [{"chunk_id": "...", "content": "..."}]

        Returns:
            [{"chunk_id": "...", "rerank_score": 0.9}] 按 score 降序
        """
        if await self.is_degraded():
            raise RuntimeError("Rerank service is in circuit-break state")
        client = await self._get_client()
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{self.endpoint}/rerank",
                    json={"query": query, "candidates": candidates},
                    timeout=settings.RERANK_TIMEOUT,
                )
                resp.raise_for_status()
                await self._on_success()
                return resp.json().get("results", [])
            except (httpx.HTTPError, KeyError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
