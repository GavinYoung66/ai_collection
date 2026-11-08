"""T93：DashScope ASR 子进程移出事件循环。

- loop-lag 断言：探测/分段阻塞期间事件循环可响应（/health 语义 < 500ms）
- 分段异常仍走降级整段直传（asr_segment_failed 语义保持）
- ffprobe 探测返回 None → 单段直传
- 多段转写拼接顺序（按时间序）保持
- 熔断 open 状态短路行为回归
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.services.asr_providers.dashscope_provider import DashScopeASRProvider


@pytest.fixture()
def provider():
    p = DashScopeASRProvider.__new__(DashScopeASRProvider)
    p._breaker_calls: list[str] = []

    async def _before_call():
        return True

    async def _record_success():
        p._breaker_calls.append("success")

    async def _record_failure(exc):
        p._breaker_calls.append("failure")

    p._breaker = SimpleNamespaceBreaker(_before_call, _record_success, _record_failure)
    return p


class SimpleNamespaceBreaker:
    def __init__(self, before_call, record_success, record_failure):
        self._before_call = before_call
        self._record_success = record_success
        self._record_failure = record_failure
        self.open = False

    async def before_call(self):
        if self.open:
            return False
        return await self._before_call()

    async def record_success(self):
        await self._record_success()

    async def record_failure(self, exc):
        await self._record_failure(exc)


def _patch_transcribe(monkeypatch, results: list[str], *, api_key="sk-test"):
    """mock _transcribe_single 记录 (fmt) 调用序列并按序返回文本。"""
    calls: list[tuple[str, str]] = []

    async def _single(self, audio: bytes, fmt: str, key: str):  # noqa: ANN001, ARG001
        calls.append((len(audio), fmt))
        return results[len(calls) - 1] if len(results) >= len(calls) else "x"

    monkeypatch.setattr(DashScopeASRProvider, "_transcribe_single", _single)
    monkeypatch.setattr(DashScopeASRProvider, "_resolve_api_key", lambda self: api_key)
    return calls


class TestLoopLag:
    @pytest.mark.asyncio
    async def test_health_stays_responsive_during_probe_and_segment(self, provider, monkeypatch):
        """探测/分段阻塞 2s 期间，事件循环探活延迟 < 500ms（spec 4.1.1）。

        双重守护：
        - 线程身份断言：探测/分段确实在事件循环线程外执行（与计时无关，
          即使 watchdog 采样碰巧错过冻结窗口也能抓住同步直调）；
        - watchdog 先建立首个采样窗口再启动任务：冻结期间挂起的 sleep 计时
          延迟返回会被记录（此前任务先启动，同步冻结发生在 watchdog 首次
          采样之前，lag 不可见，守护无效）。
        """
        calls = _patch_transcribe(monkeypatch, ["seg1", "seg2"])
        off_loop_threads: list[str] = []

        def slow_probe(*a, **k):  # noqa: ANN002, ANN003
            off_loop_threads.append(threading.current_thread().name)
            time.sleep(0.8)
            return 300.0

        def slow_segment(*a, **k):  # noqa: ANN002, ANN003
            off_loop_threads.append(threading.current_thread().name)
            time.sleep(0.8)
            return [b"seg-a", b"seg-b"]

        monkeypatch.setattr(DashScopeASRProvider, "_probe_duration", slow_probe)
        monkeypatch.setattr(DashScopeASRProvider, "_segment_audio", slow_segment)

        loop_lags: list[float] = []
        stop = asyncio.Event()

        async def watchdog():
            while not stop.is_set():
                start = time.monotonic()
                await asyncio.sleep(0.05)
                loop_lags.append(time.monotonic() - start)

        watchdog_task = asyncio.create_task(watchdog())
        await asyncio.sleep(0)  # 让 watchdog 在任务启动前取到首个 start 时间戳
        task = asyncio.create_task(provider.transcribe(b"audio", "mp3"))
        result = await task
        stop.set()
        await watchdog_task

        assert result == "seg1\nseg2"
        assert len(calls) == 2
        # 线程身份：阻塞调用不得在事件循环（主）线程内执行
        assert off_loop_threads, "probe/segment should have run"
        assert all(name != "MainThread" for name in off_loop_threads), (
            f"probe/segment ran on the event loop thread: {off_loop_threads}"
        )
        # 事件循环探活未被长任务冻结（阈值 500ms，实测远低）
        assert loop_lags, "watchdog should have sampled at least once"
        assert max(loop_lags) < 0.5, f"loop-lag too high: {max(loop_lags):.3f}s"


class TestSemanticPreservation:
    @pytest.mark.asyncio
    async def test_segment_failure_degrades_to_single_pass(self, provider, monkeypatch):
        """分段异常仍走降级整段直传（warning asr_segment_failed 语义保持）。"""
        calls = _patch_transcribe(monkeypatch, ["whole"])

        def broken_segment(*a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("ffmpeg boom")

        monkeypatch.setattr(DashScopeASRProvider, "_probe_duration", lambda *a, **k: 300.0)
        monkeypatch.setattr(DashScopeASRProvider, "_segment_audio", broken_segment)

        result = await provider.transcribe(b"audio", "mp3")
        assert result == "whole"
        assert calls == [(5, "mp3")]  # 整段直传、fmt 原样

    @pytest.mark.asyncio
    async def test_probe_none_falls_back_to_single_pass(self, provider, monkeypatch):
        """ffprobe 缺失/失败返回 None → 单段直传（fmt 原样）。"""
        calls = _patch_transcribe(monkeypatch, ["direct"])

        monkeypatch.setattr(DashScopeASRProvider, "_probe_duration", lambda *a, **k: None)

        result = await provider.transcribe(b"audio", "m4a")
        assert result == "direct"
        assert calls == [(5, "m4a")]

    @pytest.mark.asyncio
    async def test_probe_timeout_expired_degrades_to_single_pass(self, provider, monkeypatch):
        """ffprobe 超时（subprocess.TimeoutExpired）→ 探测返回 None → 单段直传（spec 4.1.1 降级）。

        真实 _probe_duration 捕获 TimeoutExpired（SubprocessError 子类）返回 None；
        通过 patch shutil.which / subprocess.run 驱动真实实现，而非伪造返回值。
        """
        import subprocess as _sp

        calls = _patch_transcribe(monkeypatch, ["direct"])
        monkeypatch.setattr(
            "app.services.asr_providers.dashscope_provider.shutil.which",
            lambda name: f"/usr/bin/{name}",
        )

        def _timeout_run(*a, **k):  # noqa: ANN002, ANN003
            raise _sp.TimeoutExpired(cmd="ffprobe", timeout=30)

        monkeypatch.setattr(
            "app.services.asr_providers.dashscope_provider.subprocess.run", _timeout_run
        )

        assert DashScopeASRProvider._probe_duration(b"audio", "mp3") is None

        result = await provider.transcribe(b"audio", "mp3")
        assert result == "direct"
        assert calls == [(5, "mp3")]

    @pytest.mark.asyncio
    async def test_multi_segment_join_order_preserved(self, provider, monkeypatch):
        """多段转写拼接顺序（按时间序）与整改前一致。"""
        calls = _patch_transcribe(monkeypatch, ["第一段", "第二段", "第三段"])

        monkeypatch.setattr(DashScopeASRProvider, "_probe_duration", lambda *a, **k: 600.0)
        monkeypatch.setattr(
            DashScopeASRProvider, "_segment_audio", lambda *a, **k: [b"s1", b"s2", b"s3"]
        )

        result = await provider.transcribe(b"audio", "mp3")
        assert result == "第一段\n第二段\n第三段"
        assert [c[0] for c in calls] == [2, 2, 2]

    @pytest.mark.asyncio
    async def test_circuit_open_short_circuits(self, provider, monkeypatch):
        """熔断 open 状态短路行为回归（before_call False → RuntimeError）。"""
        _patch_transcribe(monkeypatch, ["x"])
        provider._breaker.open = True

        with pytest.raises(RuntimeError, match="circuit-break"):
            await provider.transcribe(b"audio", "mp3")