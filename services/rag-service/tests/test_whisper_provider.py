"""Whisper Provider 单元测试（L5 / D5）。

覆盖：转写成功、模型懒加载复用、模型加载失败、转写异常、依赖缺失、
RuntimeError 契约、返回字符串类型、转写内容正确拼接；
5.4 并发首次转写只加载一次模型（threading.Lock + 双重检查）。
"""
import asyncio
import sys
from unittest.mock import MagicMock

import pytest

from app.services.asr_providers.whisper_provider import WhisperASRProvider


class FakeSegment:
    def __init__(self, text: str):
        self.text = text


class FakeModel:
    def __init__(self, fail_load: bool = False, fail_transcribe: bool = False):
        self.fail_load = fail_load
        self.fail_transcribe = fail_transcribe
        self.calls = 0

    def transcribe(self, audio_bytes):
        self.calls += 1
        if self.fail_transcribe:
            raise RuntimeError("transcribe boom")
        return [FakeSegment("你好"), FakeSegment("世界")], None


@pytest.fixture
def fake_whisper_module(monkeypatch):
    """注入 fake faster_whisper 模块（真实依赖未安装）。"""
    mod = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    return mod


class TestWhisperTranscribe:
    @pytest.mark.asyncio
    async def test_transcribe_success(self, fake_whisper_module):
        """转写成功返回拼接文本。"""
        fake_whisper_module.WhisperModel = lambda *a, **k: FakeModel()
        provider = WhisperASRProvider()
        result = await provider.transcribe(b"audio-bytes", "wav")
        assert result == "你好世界"

    @pytest.mark.asyncio
    async def test_transcribe_returns_str(self, fake_whisper_module):
        """返回类型为 str（与 DashScope Provider 契约一致）。"""
        fake_whisper_module.WhisperModel = lambda *a, **k: FakeModel()
        provider = WhisperASRProvider()
        result = await provider.transcribe(b"audio-bytes", "mp3")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_model_lazy_load_reused(self, fake_whisper_module):
        """模型懒加载后复用（第二次转写不再重建模型）。"""
        model = FakeModel()
        fake_whisper_module.WhisperModel = MagicMock(return_value=model)
        provider = WhisperASRProvider()
        await provider.transcribe(b"a", "wav")
        await provider.transcribe(b"b", "wav")
        fake_whisper_module.WhisperModel.assert_called_once()
        assert model.calls == 2

    @pytest.mark.asyncio
    async def test_model_load_failure_raises_runtime_error(self, fake_whisper_module):
        """模型加载失败 → RuntimeError。"""
        def _fail(*a, **k):
            raise RuntimeError("load boom")

        fake_whisper_module.WhisperModel = _fail
        provider = WhisperASRProvider()
        with pytest.raises(RuntimeError) as exc_info:
            await provider.transcribe(b"audio", "wav")
        assert "模型加载失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_transcribe_failure_raises_runtime_error(self, fake_whisper_module):
        """转写异常 → RuntimeError。"""
        fake_whisper_module.WhisperModel = lambda *a, **k: FakeModel(fail_transcribe=True)
        provider = WhisperASRProvider()
        with pytest.raises(RuntimeError) as exc_info:
            await provider.transcribe(b"audio", "wav")
        assert "转写失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_import_error_raises_runtime_error(self, monkeypatch):
        """faster_whisper 未安装 → RuntimeError（依赖缺失）。"""
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        provider = WhisperASRProvider()
        with pytest.raises(RuntimeError) as exc_info:
            await provider.transcribe(b"audio", "wav")
        assert "依赖未安装" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_transcribe_empty_segments(self, fake_whisper_module):
        """空分段列表 → 返回空字符串。"""

        class EmptyModel:
            def transcribe(self, audio_bytes):
                return [], None

        fake_whisper_module.WhisperModel = lambda *a, **k: EmptyModel()
        provider = WhisperASRProvider()
        assert await provider.transcribe(b"audio", "wav") == ""

    @pytest.mark.asyncio
    async def test_fmt_passthrough_does_not_affect_result(self, fake_whisper_module):
        """fmt 参数仅透传，不影响转写结果（wav/mp3/m4a 均可用）。"""
        fake_whisper_module.WhisperModel = lambda *a, **k: FakeModel()
        provider = WhisperASRProvider()
        for fmt in ("wav", "mp3", "m4a"):
            assert await provider.transcribe(b"audio", fmt) == "你好世界"

    @pytest.mark.asyncio
    async def test_whisper_model_loaded_once_concurrent(self, fake_whisper_module):
        """并发首次转写 → 模型只加载一次（threading.Lock + 双重检查，rag-service-final-fix 5.4）。"""
        model = FakeModel()
        fake_whisper_module.WhisperModel = MagicMock(return_value=model)
        provider = WhisperASRProvider()
        results = await asyncio.gather(
            provider.transcribe(b"a", "wav"),
            provider.transcribe(b"b", "wav"),
            provider.transcribe(b"c", "wav"),
        )
        fake_whisper_module.WhisperModel.assert_called_once()
        assert model.calls == 3
        assert results == ["你好世界", "你好世界", "你好世界"]
