"""DashScope Paraformer ASR Provider 实现，含重试与熔断逻辑。"""
import asyncio
import base64

import httpx
import structlog
from redis.asyncio import Redis

from app.services.asr_providers.base import ASRProvider
from common.config.settings import settings

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:asr:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60
_ASR_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/speech/asr"
_MODEL = "paraformer-v2"


class DashScopeASRProvider(ASRProvider):
    """阿里云 DashScope Paraformer 语音识别实现。

    通过 DashScope ASR REST API 将音频转写为文本，
    支持 mp3/wav/m4a 格式，包含 3 次指数退避重试与熔断逻辑
    （连续 3 次失败写入 Redis `rag:asr:degraded`，TTL 60s）。
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
        return await self._redis.exists(_DEGRADED_KEY) > 0

    async def _on_success(self) -> None:
        self._failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        self._failure_count += 1
        if self._failure_count >= _FAILURE_THRESHOLD:
            await self._set_degraded()
            logger.error(
                "asr_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
            )

    async def _set_degraded(self, ttl: int = _CIRCUIT_TTL) -> None:
        if self._redis is not None:
            await self._redis.setex(_DEGRADED_KEY, ttl, "1")

    async def transcribe(self, audio_bytes: bytes, fmt: str) -> str:
        """调用 DashScope Paraformer 转写音频为文本。

        Args:
            audio_bytes: 音频文件二进制内容。
            fmt: 音频格式，取值 {mp3, wav, m4a}。

        Returns:
            识别出的文本字符串。

        Raises:
            RuntimeError: 服务处于熔断状态或未配置 API Key。
            httpx.HTTPError: 调用持续失败（已耗尽重试次数）。
        """
        if await self._is_degraded():
            raise RuntimeError("ASR service is in circuit-break state")
        if not settings.DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY not configured")

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        headers = {
            "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": _MODEL,
            "input": {
                "audio": {
                    "data": audio_b64,
                },
            },
            "parameters": {
                "format": fmt,
            },
        }

        client = await self._get_client()
        for attempt in range(3):
            try:
                resp = await client.post(
                    _ASR_ENDPOINT,
                    headers=headers,
                    json=payload,
                    timeout=settings.ASR_TIMEOUT,
                )
                resp.raise_for_status()
                await self._on_success()
                return resp.json()["output"]["text"]
            except (httpx.HTTPError, KeyError, ValueError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
