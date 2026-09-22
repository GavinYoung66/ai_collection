"""DashScope Paraformer ASR Provider 实现，含熔断逻辑。"""
import asyncio
import base64
import os
import shutil
import subprocess
import tempfile

import httpx
import structlog
from redis.asyncio import Redis

from app.services.asr_providers.base import ASRProvider
from app.settings import settings
from common.resilience.breaker import BreakerRegistry, make_mcp_breaker_key
from common.resilience.timeout import Timeout
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()

_BREAKER_KEY = make_mcp_breaker_key("rag", "asr")
_asr_breakers = BreakerRegistry(fail_max=3, reset_timeout=60)
# 千问3-ASR-Flash 走 OpenAI 兼容 `/compatible-mode/v1/chat/completions`，
# 同步返回识别文本，且支持以 data URI（base64）直接传入本地音频，无需公网 URL/OSS，
# 是本地上传场景下最简的接入方式（Paraformer 非实时识别需公网 file_urls + 异步轮询）。
_ASR_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
_MODEL = "qwen3-asr-flash"
# 音频扩展名 -> data URI 的 MIME，用于 base64 音频识别。
_ASR_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}


class DashScopeASRProvider(ASRProvider):
    """阿里云 DashScope 千问3-ASR-Flash 语音识别实现。

    通过 DashScope 兼容 OpenAI 的 Chat Completions 接口将音频转写为文本，
    支持 mp3/wav/m4a 格式（base64 data URI 直传）。熔断由模块级
    BreakerRegistry 共享（Factory 每次 create 新实例，fail_max 必须跨实例累计）。
    """

    def __init__(self, redis: Redis | None = None):
        self._redis = redis  # 保留构造签名；进程内熔断，不写废弃全局键
        self._client: httpx.AsyncClient | None = None
        self._breaker = _asr_breakers.get_or_create(_BREAKER_KEY)
        self._timeout = Timeout(total=float(settings.ASR_TIMEOUT))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    @staticmethod
    def _resolve_api_key() -> str:
        """解析 DashScope ASR 所需 Key（对齐已配置的千问 DashScope Key）。

        统一去掉首尾空白，兼容 `.env` 中 `KEY = value` 带空格的写法，
        避免「已配置 Key 却被判空 / 被带入多余空白」导致的误报。
        返回去空白后的 Key；为空则视为未配置。
        """
        raw = getattr(settings, "DASHSCOPE_API_KEY", "") or ""
        return raw.strip()

    @staticmethod
    def _probe_duration(audio_bytes: bytes, fmt: str) -> float | None:
        """用 ffprobe 探测音频时长（秒）。

        qwen3-asr-flash 同步接口限音频 ≤5 分钟，需据此决定是否分段。
        环境缺少 ffprobe 或探测失败时返回 None（调用方降级为单段直传）。
        """
        if not shutil.which("ffprobe"):
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False)
        try:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp.close()
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", tmp.name],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode != 0:
                return None
            return float(out.stdout.strip())
        except (ValueError, OSError, subprocess.SubprocessError):
            return None
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    @staticmethod
    def _segment_audio(audio_bytes: bytes, fmt: str, segment_seconds: int = 240) -> list[bytes]:
        """用 ffmpeg 将音频切成不超过 segment_seconds 秒的 mp3 片段。

        用于把超 5 分钟的长音频切成多个小段，逐段调用同步识别后拼接，
        从而绕开 qwen3-asr-flash 的时长上限。返回按时间顺序排序的片段字节列表。
        """
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg 不可用，无法分段识别长音频")
        tmpdir = tempfile.mkdtemp(prefix="asr_seg_")
        try:
            infile = os.path.join(tmpdir, f"input.{fmt}")
            with open(infile, "wb") as f:
                f.write(audio_bytes)
            pattern = os.path.join(tmpdir, "seg_%03d.mp3")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", infile,
                 "-f", "segment", "-segment_time", str(segment_seconds),
                 "-c:a", "libmp3lame", "-b:a", "128k", pattern],
                check=True, capture_output=True, timeout=600,
            )
            segs = sorted(
                os.path.join(tmpdir, n) for n in os.listdir(tmpdir)
                if n.startswith("seg_") and n.endswith(".mp3")
            )
            if not segs:
                raise RuntimeError("ffmpeg 分段未产生任何片段")
            return [open(p, "rb").read() for p in segs]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _transcribe_single(self, audio_bytes: bytes, fmt: str, api_key: str) -> str:
        """对单个音频片段调用 DashScope 千问3-ASR-Flash（无自写退避）。"""
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        mime = _ASR_MIME.get(fmt, "audio/mpeg")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": _MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:{mime};base64,{audio_b64}",
                            },
                        }
                    ],
                }
            ],
        }

        client = await self._get_client()
        resp = await asyncio.wait_for(
            client.post(
                _ASR_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=self._timeout.total,
            ),
            timeout=self._timeout.total,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def transcribe(self, audio_bytes: bytes, fmt: str) -> str:
        """调用 DashScope 千问3-ASR-Flash 转写音频为文本。

        音频时长 ≤4 分钟时直接整段识别；超长音频通过 ffmpeg 切成 ≤4 分钟的
        mp3 片段逐段识别后拼接文本，绕开 qwen3-asr-flash 同步接口的时长上限。

        Args:
            audio_bytes: 音频文件二进制内容。
            fmt: 音频格式，取值 {mp3, wav, m4a}。

        Returns:
            识别出的文本字符串。

        Raises:
            RuntimeError: 服务处于熔断状态或未配置 API Key。
            httpx.HTTPError: HTTP 调用失败。
        """
        api_key = self._resolve_api_key()
        if not api_key:
            # 明确指明缺失配置项，便于定位；对外由 ingest 沿既有失败路径转为 A3001
            raise RuntimeError(
                "未配置 DashScope ASR Key，请设置环境变量 DASHSCOPE_API_KEY"
                "（或等价的可访问 DashScope 的千问 Key），否则音频转写无法完成"
            )

        if not await self._breaker.before_call():
            logger.warning("asr_circuit_open_skip")
            raise RuntimeError("ASR service is in circuit-break state")

        try:
            duration = self._probe_duration(audio_bytes, fmt)
            # 超过 4 分钟才需要分段（接口上限 5 分钟，保留余量）
            if duration is not None and duration > 240:
                try:
                    segments = self._segment_audio(audio_bytes, fmt)
                except Exception as e:
                    logger.warning(
                        "asr_segment_failed",
                        error=str(e),
                        trace_id=get_trace_id(),
                    )
                    segments = [audio_bytes]  # 分段失败降级为整段直传（保持原行为）
                if len(segments) > 1:
                    parts: list[str] = []
                    for seg in segments:
                        text = await self._transcribe_single(seg, "mp3", api_key)
                        if text and text.strip():
                            parts.append(text.strip())
                    result = "\n".join(parts)
                    await self._breaker.record_success()
                    return result

            result = await self._transcribe_single(audio_bytes, fmt, api_key)
            await self._breaker.record_success()
            return result
        except Exception as e:
            await self._breaker.record_failure(e)
            raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
