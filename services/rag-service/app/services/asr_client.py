"""ASR 客户端：封装 Provider 工厂、静音裁剪与分段拼接。"""
import os

import structlog
from redis.asyncio import Redis

from app.services.asr_providers import ASRFactory

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:asr:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60
_SEGMENT_SECONDS = 60
_SUPPORTED_FORMATS = {"mp3", "wav", "m4a"}


class ASRClient:
    """ASR 客户端，封装 Provider 创建、静音裁剪与分段拼接。

    职责：
    - 从文件名提取音频格式（mp3/wav/m4a）。
    - 静音裁剪：WAV 首尾 < 0.5s 静音裁剪，当前版本直接透传，
      实际 VAD 推迟到后续迭代。
    - 长音频分段：超过 60s 概念上分段，当前版本直接透传给
      Provider，由 Provider 处理。
    - 熔断：连续 3 次失败写入 Redis `rag:asr:degraded`（TTL 60s）。
    """

    def __init__(self, redis: Redis | None = None):
        self._redis = redis
        self._failure_count = 0

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
                "asr_client_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
            )

    async def _set_degraded(self, ttl: int = _CIRCUIT_TTL) -> None:
        if self._redis is not None:
            await self._redis.setex(_DEGRADED_KEY, ttl, "1")

    @staticmethod
    def _extract_format(filename: str) -> str:
        """从文件名后缀提取音频格式。

        Raises:
            ValueError: 后缀不在支持范围 {mp3, wav, m4a}。
        """
        ext = os.path.splitext(filename)[1].lstrip(".").lower()
        if ext not in _SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported audio format: {ext}")
        return ext

    @staticmethod
    def _trim_silence(audio_bytes: bytes, fmt: str) -> bytes:
        """静音裁剪（简化版）。

        WAV 格式理论上裁剪首尾 < 0.5s 静音，当前版本直接透传，
        实际 VAD 推迟到后续迭代。
        """
        return audio_bytes

    @staticmethod
    def _split_segments(audio_bytes: bytes, fmt: str) -> list[bytes]:
        """长音频分段（简化版）。

        超过 60s 的音频概念上分段，当前版本直接透传完整音频，
        交由 Provider 处理。返回单元素列表。
        """
        return [audio_bytes]

    async def transcribe(self, file_bytes: bytes, filename: str) -> str:
        """转写音频文件为文本。

        Args:
            file_bytes: 音频文件二进制内容。
            filename: 文件名，用于推断格式（mp3/wav/m4a）。

        Returns:
            识别出的文本字符串（多段拼接结果）。

        Raises:
            RuntimeError: 服务处于熔断状态。
            ValueError: 音频格式不支持。
        """
        if await self._is_degraded():
            raise RuntimeError("ASR service is in circuit-break state")

        fmt = self._extract_format(filename)
        audio_bytes = self._trim_silence(file_bytes, fmt)
        segments = self._split_segments(audio_bytes, fmt)

        provider = ASRFactory.create()
        texts: list[str] = []
        try:
            for seg in segments:
                text = await provider.transcribe(seg, fmt)
                texts.append(text)
        except Exception as e:
            await self._on_failure(e)
            raise
        await self._on_success()
        return "".join(texts)
