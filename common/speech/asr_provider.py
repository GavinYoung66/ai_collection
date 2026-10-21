"""阿里云 NLS WebSocket 流式 ASR Provider。"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import aiohttp
import structlog

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.llm.reliability import is_placeholder_secret
from common.utils.retry import retry

logger = structlog.get_logger()

_NLS_GATEWAY = "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1"


@dataclass
class ASRResult:
    text: str
    confidence: float


class ASRProvider:
    """封装阿里云 NLS 实时语音识别 WebSocket 调用。"""

    def __init__(self) -> None:
        self._app_key = settings.ASR_APP_KEY
        self._token = settings.ASR_ACCESS_TOKEN

    def _ws_url(self) -> str:
        return f"{_NLS_GATEWAY}?appkey={self._app_key}&token={self._token}&format=pcm&sample_rate=16000"

    @retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def recognize(self, audio_stream: bytes) -> ASRResult:
        """流式发送音频，返回识别文本 + 置信度。

        Args:
            audio_stream: 16kHz 16bit PCM 音频数据。

        Returns:
            ASRResult: text + confidence。
        """
        if settings.ENV == "dev" and is_placeholder_secret(self._app_key):
            logger.info("asr_mock_mode", length=len(audio_stream))
            return ASRResult(text="[mock]语音识别结果", confidence=0.95)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(self._ws_url(), timeout=5) as ws:
                    chunk_size = 3200
                    for i in range(0, len(audio_stream), chunk_size):
                        await ws.send_bytes(audio_stream[i : i + chunk_size])
                        await asyncio.sleep(0.02)
                    await ws.send_str('{"action":"stop"}')
                    text_parts: list[str] = []
                    confidence_sum = 0.0
                    count = 0
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("action") == "result":
                                result = data.get("result", "")
                                text_parts.append(result)
                                confidence_sum += float(data.get("confidence", 0.9))
                                count += 1
                                if data.get("isSentenceEnd"):
                                    break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            break
                    confidence = confidence_sum / count if count else 0.0
                    return ASRResult(text="".join(text_parts), confidence=confidence)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.error("asr_failed", error=str(exc))
            raise BizException(ErrorCode.A1013, "语音链路异常") from exc


asr_provider = ASRProvider()
