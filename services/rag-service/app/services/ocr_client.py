"""OCR 客户端：HTTP 调用 ocr-service:8200/ocr，含熔断逻辑。"""
import asyncio

import httpx
import structlog
from redis.asyncio import Redis

from app.settings import settings
from common.resilience.breaker import CircuitBreaker, make_mcp_breaker_key
from common.resilience.timeout import Timeout

logger = structlog.get_logger()

_BREAKER_KEY = make_mcp_breaker_key("rag", "ocr")


class OCRClient:
    def __init__(
        self,
        endpoint: str | None = None,
        redis: Redis | None = None,
    ):
        self.endpoint = endpoint or settings.OCR_ENDPOINT
        self._redis = redis  # 保留构造签名；进程内熔断，不写废弃全局键
        self._client: httpx.AsyncClient | None = None
        self._breaker = CircuitBreaker(_BREAKER_KEY, fail_max=3, reset_timeout=60)
        self._timeout = Timeout(total=float(settings.OCR_TIMEOUT))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def recognize(self, file_bytes: bytes, filename: str) -> str:
        """调用 OCR 服务识别图片文本。

        熔断 open 时短路 HTTP 并抛 RuntimeError（入库映射 A3001/占位），
        MUST NOT 返回空串以免被当成空白图成功（A3002）。
        成功识别的空文本（真空白图）仍返回 ""。
        """
        if not await self._breaker.before_call():
            logger.warning("ocr_circuit_open_skip")
            raise RuntimeError("OCR service is in circuit-break state")
        try:
            client = await self._get_client()
            resp = await asyncio.wait_for(
                client.post(
                    self.endpoint,
                    files={"file": (filename, file_bytes)},
                    data={"lang": "ch"},
                    timeout=self._timeout.total,
                ),
                timeout=self._timeout.total,
            )
            resp.raise_for_status()
            text = resp.json()["text"]
            if text is None:
                raise RuntimeError("OCR service returned null text")
            await self._breaker.record_success()
            return text
        except Exception as e:
            await self._breaker.record_failure(e)
            logger.warning("ocr_http_failed", error=str(e))
            raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
