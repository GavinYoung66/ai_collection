"""Rerank 客户端：HTTP 调用 rerank-service:8101，含熔断逻辑。"""
import asyncio

import httpx
import structlog
from redis.asyncio import Redis

from app.settings import settings
from common.resilience.breaker import CircuitBreaker, make_mcp_breaker_key
from common.resilience.timeout import Timeout

logger = structlog.get_logger()

_BREAKER_KEY = make_mcp_breaker_key("rag", "rerank")


class RerankClient:
    def __init__(
        self,
        endpoint: str | None = None,
        redis: Redis | None = None,
    ):
        self.endpoint = endpoint or settings.RERANK_SERVICE_URL
        self._redis = redis  # 保留构造签名；进程内熔断，不写废弃全局键
        self._client: httpx.AsyncClient | None = None
        self._breaker = CircuitBreaker(_BREAKER_KEY, fail_max=3, reset_timeout=60)
        self._timeout = Timeout(total=float(settings.RERANK_TIMEOUT))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    def _open_blocks_calls(self) -> bool:
        """True when OPEN and not yet eligible for probe; never mutates state."""
        if self._breaker.state != "open":
            return False
        opened_at = self._breaker._opened_at
        if opened_at is None:
            return True
        return (self._breaker._clock() - opened_at) < self._breaker.reset_timeout

    async def is_degraded(self) -> bool:
        try:
            return self._open_blocks_calls()
        except Exception:
            return False

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
        if not await self._breaker.before_call():
            raise RuntimeError("Rerank service is in circuit-break state")
        try:
            client = await self._get_client()
            resp = await asyncio.wait_for(
                client.post(
                    f"{self.endpoint}/rerank",
                    json={"query": query, "candidates": candidates},
                    timeout=self._timeout.total,
                ),
                timeout=self._timeout.total,
            )
            resp.raise_for_status()
            await self._breaker.record_success()
            return resp.json().get("results", [])
        except Exception as e:
            await self._breaker.record_failure(e)
            raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
