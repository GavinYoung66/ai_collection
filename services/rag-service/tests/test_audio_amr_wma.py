"""AMR/WMA 音频解码转写单元测试（V5，design D6；tasks 11.5）。

覆盖：AMR/WMA ffmpeg 解码 → 复用 VAD/ASR 管线转写（mock 解码与 ASR Provider）/
损坏音频 → ValueError（ingest 转 A3001）且不卡 worker /
KB_ASR_AMR_WMA_ENABLED=false → 白名单外 A3001（V4 行为）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.parsers.audio_parser import AudioParser
from app.services.audio_decoder import AudioDecodeError
from common.config.settings import settings


class TestAudioAmrWma:
    @pytest.mark.asyncio
    async def test_amr_decodes_and_transcribes(self, tmp_path, monkeypatch):
        """KB_ASR_AMR_WMA_ENABLED=true：ffmpeg 解码 → ASR 管线转写（filename=decoded.wav）。"""
        monkeypatch.setattr(settings, "KB_ASR_AMR_WMA_ENABLED", True)
        amr = tmp_path / "rec.amr"
        amr.write_bytes(b"amr-bytes")
        asr_client = MagicMock()
        asr_client.transcribe = AsyncMock(return_value="AMR 转写的通话文本")
        with patch("app.services.audio_decoder.decode_to_wav", AsyncMock(return_value=b"wav-bytes")) as mock_decode:
            parser = AudioParser(asr_client=asr_client)
            text = await parser.extract_text(amr)
        assert text == "AMR 转写的通话文本"
        mock_decode.assert_awaited_once_with(amr)
        # 复用既有 ASR 管线：解码后按 decoded.wav 交给 transcribe
        asr_client.transcribe.assert_awaited_once_with(b"wav-bytes", "decoded.wav")

    @pytest.mark.asyncio
    async def test_wma_decodes_and_transcribes(self, tmp_path, monkeypatch):
        """WMA 与 AMR 同路径解码转写。"""
        monkeypatch.setattr(settings, "KB_ASR_AMR_WMA_ENABLED", True)
        wma = tmp_path / "rec.wma"
        wma.write_bytes(b"wma-bytes")
        asr_client = MagicMock()
        asr_client.transcribe = AsyncMock(return_value="WMA 转写文本")
        with patch("app.services.audio_decoder.decode_to_wav", AsyncMock(return_value=b"wav")):
            parser = AudioParser(asr_client=asr_client)
            text = await parser.extract_text(wma)
        assert text == "WMA 转写文本"

    @pytest.mark.asyncio
    async def test_corrupt_audio_raises_value_error(self, tmp_path, monkeypatch):
        """损坏 AMR：解码抛 AudioDecodeError → extract_text 抛 ValueError（ingest 转 A3001），worker 不卡死。"""
        monkeypatch.setattr(settings, "KB_ASR_AMR_WMA_ENABLED", True)
        amr = tmp_path / "corrupt.amr"
        amr.write_bytes(b"junk")
        with patch(
            "app.services.audio_decoder.decode_to_wav",
            AsyncMock(side_effect=AudioDecodeError("ffmpeg 解码失败")),
        ):
            parser = AudioParser(asr_client=MagicMock())
            with pytest.raises(ValueError, match="AMR/WMA 解码失败"):
                await parser.extract_text(amr)

    @pytest.mark.asyncio
    async def test_switch_off_rejects_amr(self, tmp_path, monkeypatch):
        """KB_ASR_AMR_WMA_ENABLED=false → AMR 抛 ValueError（白名单外 A3001，V4 行为）。"""
        monkeypatch.setattr(settings, "KB_ASR_AMR_WMA_ENABLED", False)
        amr = tmp_path / "rec.amr"
        amr.write_bytes(b"x")
        parser = AudioParser(asr_client=MagicMock())
        with pytest.raises(ValueError, match="AMR/WMA 解码未开启"):
            await parser.extract_text(amr)

    @pytest.mark.asyncio
    async def test_mp3_unaffected_by_switch(self, tmp_path, monkeypatch):
        """已支持格式（mp3）不受 AMR/WMA 开关影响，直通原 ASR 路径。"""
        monkeypatch.setattr(settings, "KB_ASR_AMR_WMA_ENABLED", False)
        mp3 = tmp_path / "rec.mp3"
        mp3.write_bytes(b"mp3")
        asr_client = MagicMock()
        asr_client.transcribe = AsyncMock(return_value="mp3 文本")
        parser = AudioParser(asr_client=asr_client)
        text = await parser.extract_text(mp3)
        assert text == "mp3 文本"
        asr_client.transcribe.assert_awaited_once_with(b"mp3", "rec.mp3")
