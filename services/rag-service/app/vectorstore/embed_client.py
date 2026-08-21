"""Embedding 客户端：HTTP 调用 embedding-service:8100，含熔断逻辑。"""
import asyncio

import httpx
import structlog
from redis.asyncio import Redis

from common.config.settings import settings

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:embed:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60


class EmbedClient:
    def __init__(
        self,
        endpoint: str | None = None,
        redis: Redis | None = None,
    ):
        self.endpoint = endpoint or settings.EMBEDDING_SERVICE_URL
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
        return await self._redis.exists(_DEGRADED_KEY) > 0

    async def _on_success(self) -> None:
        self._failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        self._failure_count += 1
        if self._failure_count >= _FAILURE_THRESHOLD:
            await self._set_degraded()
            logger.error(
                "embed_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
            )

    async def _set_degraded(self, ttl: int = _CIRCUIT_TTL) -> None:
        if self._redis is not None:
            await self._redis.setex(_DEGRADED_KEY, ttl, "1")

    async def embed(self, text: str) -> list[float]:
        if await self._is_degraded():
            raise RuntimeError("Embedding service is in circuit-break state")
        client = await self._get_client()
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{self.endpoint}/embed",
                    json={"texts": [text]},
                    timeout=settings.EMBED_TIMEOUT,
                )
                resp.raise_for_status()
                await self._on_success()
                return resp.json()["vectors"][0]
            except (httpx.HTTPError, KeyError, IndexError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        if await self._is_degraded():
            raise RuntimeError("Embedding service is in circuit-break state")
        client = await self._get_client()
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{self.endpoint}/embed",
                    json={"texts": texts},
                    timeout=settings.EMBED_BATCH_TIMEOUT,
                )
                resp.raise_for_status()
                await self._on_success()
                return resp.json()["vectors"]
            except (httpx.HTTPError, KeyError, IndexError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
