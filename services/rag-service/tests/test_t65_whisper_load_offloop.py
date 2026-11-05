"""T65 / 6.4：Whisper 模型加载移入线程池 单测。

验收判据：mock WhisperModel 加载耗时 3s，首次转写期间事件循环保持响应
（并发探测协程 1s 内完成，而非被同步加载阻塞 3s）；模型仅构造一次。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from app.services.asr_providers.whisper_provider import (
    WhisperASRProvider,
)


@pytest.fixture(autouse=True)
def _reset_model_lock():
    """重置模块级 asyncio.Lock 与 provider._model，防跨用例串扰。"""
    global _MODEL_LOCK
    import app.services.asr_providers.whisper_provider as wp_mod

    wp_mod._MODEL_LOCK = asyncio.Lock()
    yield
    wp_mod._MODEL_LOCK = asyncio.Lock()


@pytest.mark.asyncio
async def test_model_load_off_event_loop(monkeypatch):
    """加载移入 to_thread：3s 加载期间事件循环保持响应（探测 <1s 完成）。"""
    fake = types.ModuleType("faster_whisper")
    constructed: list = []

    class FakeModel:
        def transcribe(self, audio):
            # faster-whisper 返回 (segments_generator, info) 二元组
            return [], types.SimpleNamespace()

    def slow_factory(*args, **kwargs):
        time.sleep(3)  # 模拟 tiny 模型冷加载数秒
        model = FakeModel()
        constructed.append(model)
        return model

    fake.WhisperModel = slow_factory
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    provider = WhisperASRProvider()
    load_task = asyncio.create_task(provider.transcribe(b"audio-bytes", "wav"))
    await asyncio.sleep(0.2)  # 加载进行中

    probe = asyncio.Event()

    async def _probe():
        await asyncio.sleep(0.05)
        probe.set()

    probe_task = asyncio.create_task(_probe())
    await asyncio.wait_for(probe.wait(), timeout=1.0), (
        "事件循环被同步模型加载阻塞（/health 与并发请求在加载期间不可响应）"
    )

    result = await asyncio.wait_for(load_task, timeout=6)
    assert result == ""
    assert len(constructed) == 1, "并发首载仅构造一次（asyncio.Lock 门控）"
    probe_task.cancel()


@pytest.mark.asyncio
async def test_concurrent_first_transcribe_loads_once(monkeypatch):
    """两个并发首转写：模型加载恰好一次（asyncio.Lock 双重检查）。"""
    fake = types.ModuleType("faster_whisper")
    constructed: list = []

    class FakeModel:
        def transcribe(self, audio):
            # faster-whisper 返回 (segments, info)；segments 元素含 .text
            segments = [types.SimpleNamespace(text="文本")]
            return segments, types.SimpleNamespace()

    def slow_factory(*args, **kwargs):
        time.sleep(0.3)
        model = FakeModel()
        constructed.append(model)
        return model

    fake.WhisperModel = slow_factory
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    provider = WhisperASRProvider()
    results = await asyncio.gather(
        provider.transcribe(b"a", "wav"),
        provider.transcribe(b"b", "wav"),
    )
    assert results == ["文本", "文本"]
    assert len(constructed) == 1