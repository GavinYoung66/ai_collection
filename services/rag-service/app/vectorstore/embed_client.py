"""Embedding 客户端：HTTP 调用 embedding-service:8100，含熔断逻辑。"""
import asyncio

import httpx
import structlog
from redis.asyncio import Redis

from app.settings import settings
from common.resilience.breaker import CircuitBreaker, make_mcp_breaker_key
from common.resilience.timeout import Timeout

logger = structlog.get_logger()

_BREAKER_KEY = make_mcp_breaker_key("rag", "embed")


class EmbedClient:
    def __init__(
        self,
        endpoint: str | None = None,
        redis: Redis | None = None,
    ):
        self.endpoint = endpoint or settings.EMBEDDING_SERVICE_URL
        self._redis = redis  # 保留构造签名；进程内熔断，不写废弃全局键
        self._client: httpx.AsyncClient | None = None
        self._breaker = CircuitBreaker(_BREAKER_KEY, fail_max=3, reset_timeout=60)
        self._timeout = Timeout(total=float(settings.EMBED_TIMEOUT))
        self._batch_timeout = Timeout(total=float(settings.EMBED_BATCH_TIMEOUT))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def _post(self, payload: dict, timeout: Timeout) -> httpx.Response:
        client = await self._get_client()
        return await asyncio.wait_for(
            client.post(
                f"{self.endpoint}/embed",
                json=payload,
                timeout=timeout.total,
            ),
            timeout=timeout.total,
        )

    async def embed(self, text: str) -> list[float]:
        if not await self._breaker.before_call():
            logger.warning("embed_circuit_open_skip")
            return []
        try:
            resp = await self._post({"texts": [text]}, self._timeout)
            resp.raise_for_status()
            vector = resp.json()["vectors"][0]
            if not vector:
                raise RuntimeError("Embedding service returned empty vector")
            await self._breaker.record_success()
            return vector
        except Exception as e:
            await self._breaker.record_failure(e)
            logger.warning("embed_http_failed", error=str(e))
            return []

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not await self._breaker.before_call():
            logger.warning("embed_circuit_open_skip_batch")
            return []
        try:
            resp = await self._post({"texts": texts}, self._batch_timeout)
            resp.raise_for_status()
            vectors = resp.json()["vectors"]
            if not vectors:
                raise RuntimeError("Embedding service returned empty vectors")
            await self._breaker.record_success()
            return vectors
        except Exception as e:
            await self._breaker.record_failure(e)
            logger.warning("embed_batch_http_failed", error=str(e))
            return []

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
