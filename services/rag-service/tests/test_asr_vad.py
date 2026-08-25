"""ASR VAD 静音裁剪与 60s 分片单元测试（M1 / D4）。

覆盖：
- WAV(PCM) 首尾静音裁剪
- 全静音 / 非 WAV 透传
- ≤60s 单分片兼容、>60s 硬分片边界
- transcribe 逐片转写后按时间序拼接
"""
import struct
from unittest.mock import AsyncMock

import pytest

from app.services.asr_client import ASRClient


def _make_wav(duration_sec: float, sample_rate: int = 16000, amplitude: int = 10000) -> bytes:
    """生成 mono 16-bit PCM WAV；amplitude=0 表示纯静音。"""
    n_frames = int(sample_rate * duration_sec)
    if amplitude == 0:
        pcm = b"\x00\x00" * n_frames
    else:
        pcm = struct.pack("<h", amplitude) * n_frames
    return ASRClient._build_wav(pcm, sample_rate, 1)


def _make_mixed_wav(lead: float, sound: float, trail: float, sample_rate: int = 16000) -> bytes:
    """生成「静音 + 有声 + 静音」WAV。"""
    pcm = (
        b"\x00\x00" * int(sample_rate * lead)
        + struct.pack("<h", 10000) * int(sample_rate * sound)
        + b"\x00\x00" * int(sample_rate * trail)
    )
    return ASRClient._build_wav(pcm, sample_rate, 1)


class TestTrimSilence:
    def test_wav_trim_leading_trailing_silence(self):
        """有声段首尾静音被裁剪，结果短于原音频。"""
        wav = _make_mixed_wav(0.5, 1.0, 0.5)
        trimmed = ASRClient._trim_silence(wav, "wav")
        assert trimmed.startswith(b"RIFF")
        assert len(trimmed) < len(wav)

    def test_wav_all_silent_passthrough(self):
        """全静音 WAV 透传原样。"""
        wav = _make_wav(1.0, amplitude=0)
        assert ASRClient._trim_silence(wav, "wav") == wav

    def test_non_wav_passthrough(self):
        """非 WAV（mp3/m4a）静音裁剪透传。"""
        assert ASRClient._trim_silence(b"fake-mp3", "mp3") == b"fake-mp3"
        assert ASRClient._trim_silence(b"fake-m4a", "m4a") == b"fake-m4a"


class TestSplitSegments:
    def test_wav_60s_hard_split_boundary(self):
        """130s WAV → 3 个 ≤60s 分片（60/60/10）。"""
        wav = _make_wav(130.0, sample_rate=1000)
        segments = ASRClient._split_segments(wav, "wav")
        assert len(segments) == 3

    def test_wav_short_single_segment(self):
        """≤60s WAV → 单分片，与原音频一致。"""
        wav = _make_wav(20.0, sample_rate=1000)
        segments = ASRClient._split_segments(wav, "wav")
        assert len(segments) == 1
        assert segments[0] == wav

    def test_non_wav_passthrough_single(self):
        """非 WAV 透传单元素列表。"""
        assert ASRClient._split_segments(b"fake", "mp3") == [b"fake"]


class TestTranscribeConcat:
    @pytest.mark.asyncio
    async def test_transcribe_concatenates_segments(self, monkeypatch):
        """61s WAV → 2 分片，逐片转写后按时间序拼接完整。"""
        wav = _make_wav(61.0, sample_rate=1000)
        client = ASRClient(redis=None)
        client._is_degraded = AsyncMock(return_value=False)

        class MockProvider:
            def __init__(self):
                self.calls = 0

            async def transcribe(self, audio_bytes, fmt):
                self.calls += 1
                return f"seg{self.calls}"

        provider = MockProvider()
        monkeypatch.setattr(
            "app.services.asr_client.ASRFactory.create", lambda: provider
        )
        result = await client.transcribe(wav, "test.wav")
        assert result == "seg1seg2"
        assert provider.calls == 2
