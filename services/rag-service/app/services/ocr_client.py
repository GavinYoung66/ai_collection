"""OCR 客户端：HTTP 调用 ocr-service:8200/ocr，含熔断逻辑。"""
import asyncio

import httpx
import structlog
from redis.asyncio import Redis

from app.settings import settings

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:ocr:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60


class OCRClient:
    def __init__(
        self,
        endpoint: str | None = None,
        redis: Redis | None = None,
    ):
        self.endpoint = endpoint or settings.OCR_ENDPOINT
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
                "ocr_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
            )

    async def _set_degraded(self, ttl: int = _CIRCUIT_TTL) -> None:
        if self._redis is not None:
            await self._redis.setex(_DEGRADED_KEY, ttl, "1")

    async def recognize(self, file_bytes: bytes, filename: str) -> str:
        """调用 OCR 服务识别图片文本，连续 3 次失败触发熔断。

        使用 form-data 上传文件至 OCR_ENDPOINT，超时 settings.OCR_TIMEOUT，
        失败按 1s/2s/4s 指数退避重试 3 次；3 连失败写入 Redis
        `rag:ocr:degraded`（TTL 60s）实现熔断短路。
        """
        if await self._is_degraded():
            raise RuntimeError("OCR service is in circuit-break state")
        client = await self._get_client()
        for attempt in range(3):
            try:
                resp = await client.post(
                    self.endpoint,
                    files={"file": (filename, file_bytes)},
                    data={"lang": "ch"},
                    timeout=settings.OCR_TIMEOUT,
                )
                resp.raise_for_status()
                await self._on_success()
                return resp.json()["text"]
            except (httpx.HTTPError, KeyError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
