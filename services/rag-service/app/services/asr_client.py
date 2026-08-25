"""ASR 客户端：封装 Provider 工厂、静音裁剪、分段拼接与开场白规则裁剪。

V4 D8 增量：转写文本拼接完成后执行"通话开场白"规则裁剪
（`KB_ASR_OPENING_TRIM_ENABLED=true` 生效，规则见 conf/asr_opening_rules.yaml，
纯正则+关键词表，零 LLM、零新第三方依赖；开关关闭 = V3 行为）。
"""
import os
import re
import struct
from pathlib import Path

import structlog
import yaml
from redis.asyncio import Redis

from app.services.asr_providers import ASRFactory
from common.config.settings import settings

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:asr:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60
_SEGMENT_SECONDS = 60
_SUPPORTED_FORMATS = {"mp3", "wav", "m4a"}

# VAD 静音裁剪参数（对齐 design D4：RMS 能量阈值 <0.01 判静音，窗口 0.1s）
_RMS_THRESHOLD = 0.01
_WINDOW_SECONDS = 0.1
_PCM_BITS = 16

# 开场白规则配置路径：services/rag-service/conf/asr_opening_rules.yaml
_DEFAULT_OPENING_RULES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "conf" / "asr_opening_rules.yaml"
)

# 开场白短语之间的分隔标点/空白（跳过后再尝试匹配，避免误吞正文开头标点）
_SEPARATOR_RE = re.compile(r"[\s，,。.!！?？、；;：:]+")


class ASRClient:
    """ASR 客户端，封装 Provider 创建、静音裁剪与分段拼接。

    职责：
    - 从文件名提取音频格式（mp3/wav/m4a）。
    - 静音裁剪：仅 WAV(PCM) 按 RMS 能量阈值裁剪首尾静音（窗口 0.1s），
      非 WAV 透传（MP3/M4A 无解包能力，依赖 Provider 侧处理）。
    - 长音频分段：WAV 按 ≤60s 硬分片，非 WAV 透传单元素。
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
    def _parse_wav(audio_bytes: bytes) -> dict | None:
        """解析 WAV PCM 头部，返回 {sample_rate, num_channels, bits_per_sample, data}。

        仅支持 16-bit 线性 PCM（单/双声道）；非 WAV 或解析失败返回 None（调用方透传）。
        """
        if (
            len(audio_bytes) < 44
            or audio_bytes[:4] != b"RIFF"
            or audio_bytes[8:12] != b"WAVE"
        ):
            return None
        sample_rate: int | None = None
        num_channels: int | None = None
        bits_per_sample: int | None = None
        data = b""
        pos = 12
        try:
            while pos + 8 <= len(audio_bytes):
                chunk_id = audio_bytes[pos:pos + 4]
                chunk_size = struct.unpack("<I", audio_bytes[pos + 4:pos + 8])[0]
                body = audio_bytes[pos + 8:pos + 8 + chunk_size]
                if chunk_id == b"fmt ":
                    if chunk_size < 16:
                        return None
                    audio_format = struct.unpack("<H", body[0:2])[0]
                    if audio_format != 1:  # 仅 PCM
                        return None
                    num_channels = struct.unpack("<H", body[2:4])[0]
                    sample_rate = struct.unpack("<I", body[4:8])[0]
                    bits_per_sample = struct.unpack("<H", body[14:16])[0]
                elif chunk_id == b"data":
                    data = body
                    break
                pos += 8 + chunk_size + (chunk_size & 1)
        except Exception:
            return None

        if (
            sample_rate is None
            or num_channels is None
            or bits_per_sample != _PCM_BITS
            or not data
        ):
            return None
        return {
            "sample_rate": sample_rate,
            "num_channels": num_channels,
            "bits_per_sample": bits_per_sample,
            "data": data,
        }

    @staticmethod
    def _build_wav(
        pcm_data: bytes, sample_rate: int, num_channels: int, bits_per_sample: int = _PCM_BITS
    ) -> bytes:
        """用 16-bit PCM 采样重建标准 44 字节头 WAV 字节流。"""
        byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
        block_align = num_channels * (bits_per_sample // 8)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + len(pcm_data),
            b"WAVE",
            b"fmt ",
            16,
            1,  # PCM
            num_channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            b"data",
            len(pcm_data),
        )
        return header + pcm_data

    @staticmethod
    def _trim_silence(audio_bytes: bytes, fmt: str) -> bytes:
        """静音裁剪：仅 WAV(PCM) 按 RMS 能量阈值裁剪首尾静音（窗口 0.1s）。

        非 WAV 格式透传（MP3/M4A 无解包能力）；全静音或解析失败透传原样。
        """
        if fmt != "wav":
            return audio_bytes

        parsed = ASRClient._parse_wav(audio_bytes)
        if parsed is None:
            return audio_bytes

        pcm = parsed["data"]
        sample_rate = parsed["sample_rate"]
        num_channels = parsed["num_channels"]
        frame_size = (_PCM_BITS // 8) * num_channels
        n_frames = len(pcm) // frame_size
        window_frames = max(1, int(sample_rate * _WINDOW_SECONDS))
        n_windows = (n_frames + window_frames - 1) // window_frames

        # 每窗口取首声道采样计算归一化 RMS
        rms_values: list[float] = []
        for w in range(n_windows):
            start = w * window_frames
            end = min(start + window_frames, n_frames)
            total = 0.0
            cnt = 0
            for i in range(start, end):
                offset = i * frame_size
                sample = struct.unpack_from("<h", pcm, offset)[0]
                norm = sample / 32768.0
                total += norm * norm
                cnt += 1
            rms_values.append((total / cnt) ** 0.5 if cnt else 0.0)

        first = -1
        for i, r in enumerate(rms_values):
            if r >= _RMS_THRESHOLD:
                first = i
                break
        if first == -1:
            return audio_bytes  # 全静音保持原样

        last = first
        for i in range(len(rms_values) - 1, first - 1, -1):
            if rms_values[i] >= _RMS_THRESHOLD:
                last = i
                break

        start_byte = first * window_frames * frame_size
        end_byte = min((last + 1) * window_frames * frame_size, len(pcm))
        trimmed = pcm[start_byte:end_byte]
        if not trimmed:
            return audio_bytes
        return ASRClient._build_wav(trimmed, sample_rate, num_channels)

    @staticmethod
    def _split_segments(audio_bytes: bytes, fmt: str) -> list[bytes]:
        """长音频分段：WAV 按 ≤60s 硬分片（60s×采样率×字节宽边界），非 WAV 透传单元素。"""
        if fmt != "wav":
            return [audio_bytes]

        parsed = ASRClient._parse_wav(audio_bytes)
        if parsed is None:
            return [audio_bytes]

        pcm = parsed["data"]
        sample_rate = parsed["sample_rate"]
        num_channels = parsed["num_channels"]
        frame_size = (_PCM_BITS // 8) * num_channels
        segment_bytes = sample_rate * _SEGMENT_SECONDS * frame_size
        if segment_bytes <= 0 or len(pcm) <= segment_bytes:
            return [audio_bytes]

        segments = []
        for start in range(0, len(pcm), segment_bytes):
            seg_pcm = pcm[start:start + segment_bytes]
            if not seg_pcm:
                continue
            segments.append(
                ASRClient._build_wav(seg_pcm, sample_rate, num_channels)
            )
        return segments or [audio_bytes]

    @staticmethod
    def load_opening_rules(path: str | Path | None = None) -> dict:
        """加载开场白规则配置（conf/asr_opening_rules.yaml）。

        缺失/解析失败/无 patterns → 返回空 dict（按开关关闭处理，不裁剪）。
        """
        p = Path(path or _DEFAULT_OPENING_RULES_PATH)
        if not p.exists():
            logger.warning("asr_opening_rules_not_found", path=str(p))
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict) or not data.get("patterns"):
                logger.warning("asr_opening_rules_empty", path=str(p))
                return {}
            return data
        except (yaml.YAMLError, OSError) as e:
            logger.warning("asr_opening_rules_load_failed", path=str(p), error=str(e))
            return {}

    @staticmethod
    def _match_at(patterns: list[re.Pattern], leading: str, pos: int) -> tuple[bool, int]:
        """在 pos 处依次尝试各规则正则，返回 (是否命中, 新偏移)。"""
        for pat in patterns:
            m = pat.match(leading, pos)
            if m:
                return True, m.end()
        return False, pos

    @classmethod
    def trim_opening_text(cls, text: str, rules: dict) -> str:
        """V4 D8：通话开场白规则裁剪（纯规则，零 LLM）。

        算法（保守设计，对齐 conf/asr_opening_rules.yaml 注释）：
        - 仅在文本开头 leading_max_len 字符窗口内匹配（文本短于窗口时全量匹配），不扫描全文；
        - 逐段消费开场模式（问候语/身份确认/公司名/客套语），
          短语之间允许分隔标点（仅当标点后能命中规则时才消费，避免误吞正文开头标点）；
        - 首个不命中的位置即正文起点；无任何开场模式命中 → 原样返回（不误删）；
        - 裁剪成功后清理开场白与业务内容之间的分隔标点；
        - 裁剪后剩余文本少于 trim_min_remain_len 或为空 → 保底不裁（不误删有效业务内容）。
        """
        if not rules:
            return text
        leading_max_len = int(rules.get("leading_max_len", 60))
        trim_min_remain_len = int(rules.get("trim_min_remain_len", 10))

        leading = text[:leading_max_len]
        patterns = [
            re.compile(p["regex"])
            for p in rules.get("patterns", [])
            if p.get("regex")
        ]
        if not patterns:
            return text

        offset = 0
        while offset < len(leading):
            hit, new_offset = cls._match_at(patterns, leading, offset)
            if hit:
                offset = new_offset
                continue
            # 跳过开场白之间的分隔标点后继续尝试（如"喂，你好"），
            # 标点后仍不命中则立即停止，保留原起点标点不吞
            sep = _SEPARATOR_RE.match(leading, offset)
            if sep:
                hit, new_offset = cls._match_at(patterns, leading, sep.end())
                if hit:
                    offset = new_offset
                    continue
            break

        if offset == 0:
            # 无任何开场模式命中 → 原样返回（不误删有效业务内容）
            return text

        # 开场白被裁剪：清理其与业务内容之间的分隔标点
        trimmed = text[offset:].lstrip(" \t\r\n，,。.!！?？、；;：:")
        if len(trimmed.strip()) < trim_min_remain_len:
            return text  # 保底：裁剪后过短，判定误识别，不裁剪
        return trimmed

    async def transcribe(self, file_bytes: bytes, filename: str) -> str:
        """转写音频文件为文本。

        Args:
            file_bytes: 音频文件二进制内容。
            filename: 文件名，用于推断格式（mp3/wav/m4a）。

        Returns:
            识别出的文本字符串（多段拼接结果；开关开启时已裁剪通话开场白）。

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

        full_text = "".join(texts)

        # V4 D8: 转写文本拼接完成后执行"通话开场白"规则裁剪（开关开启时生效；
        # 规则缺失/裁剪后过短 → 保底不裁，不引入 LLM/新第三方依赖）
        if settings.KB_ASR_OPENING_TRIM_ENABLED:
            rules = self.load_opening_rules()
            full_text = self.trim_opening_text(full_text, rules)

        return full_text
