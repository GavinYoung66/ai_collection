"""阿里云 NLS HTTP TTS Provider。"""
from __future__ import annotations

import asyncio
import uuid

import aiohttp
import structlog

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.llm.reliability import is_placeholder_secret
from common.utils.retry import retry

logger = structlog.get_logger()

_TTS_URL = "https://nls-gateway.cn-shanghai.aliyuncs.com/rest/v1/tts"


class TTSProvider:
    """封装阿里云 NLS 语音合成 HTTP 调用。"""

    def __init__(self) -> None:
        self._app_key = settings.TTS_APP_KEY
        self._token = settings.TTS_ACCESS_TOKEN

    @retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def synthesize(self, text: str, voice: str = "xiaoyun") -> bytes:
        """文本转语音，返回 MP3 音频 bytes。

        Args:
            text: 待合成文本（≤500 字）。
            voice: 发音人，默认 xiaoyun。

        Returns:
            bytes: MP3 音频数据。
        """
        if settings.ENV == "dev" and is_placeholder_secret(self._app_key):
            logger.info("tts_mock_mode", text_len=len(text))
            return b"mock_audio_bytes"

        task_id = str(uuid.uuid4())
        payload = {
            "appkey": self._app_key,
            "token": self._token,
            "text": text,
            "voice": voice,
            "format": "mp3",
            "sample_rate": 16000,
            "task_id": task_id,
        }
        headers = {
            "Content-Type": "application/json",
            "X-NLS-Token": self._token,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _TTS_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        raise BizException(ErrorCode.A1013, f"TTS 合成失败: HTTP {resp.status}")
                    return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.error("tts_failed", error=str(exc))
            raise BizException(ErrorCode.A1013, "语音链路异常") from exc


tts_provider = TTSProvider()
