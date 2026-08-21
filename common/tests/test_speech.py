"""common.speech 模块单元测试。"""
import pytest

from common.speech.asr_provider import ASRProvider, ASRResult
from common.speech.tts_provider import TTSProvider


class TestASRProvider:
    @pytest.mark.asyncio()
    async def test_mock_recognize(self):
        provider = ASRProvider()
        result = await provider.recognize(b"fake_audio_data")
        assert isinstance(result, ASRResult)
        assert len(result.text) > 0
        assert 0.0 <= result.confidence <= 1.0

    @pytest.mark.asyncio()
    async def test_mock_result_has_text(self):
        provider = ASRProvider()
        result = await provider.recognize(b"")
        assert result.text != ""


class TestTTSProvider:
    @pytest.mark.asyncio()
    async def test_mock_synthesize(self):
        provider = TTSProvider()
        audio = await provider.synthesize("测试文本")
        assert isinstance(audio, bytes)
        assert len(audio) > 0

    @pytest.mark.asyncio()
    async def test_mock_synthesize_empty_text(self):
        provider = TTSProvider()
        audio = await provider.synthesize("")
        assert isinstance(audio, bytes)
