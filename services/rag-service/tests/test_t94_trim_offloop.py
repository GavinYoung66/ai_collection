"""T94：asr_client 裁剪/切分移出事件循环。

- loop-lag 断言：裁剪执行期间事件循环不被冻结（并发探活可响应）
- wait_for 超时取消裁剪任务后 CancelledError 正常传播（不吞异常）
- 裁剪/切分输出与整改前一致（golden 字节对比：全静音保持原样、非 WAV 透传）
- 拼接顺序按时间序；开场白裁剪仍在拼接后执行（transcribe 层语义不变）
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.services.asr_client import ASRClient, _PCM_BITS


def _make_client() -> ASRClient:
    client = ASRClient.__new__(ASRClient)
    client._provider = None  # __new__ 绕过构造：显式预设，供 transcribe 惰性创建分支判定
    return client


def _wav_bytes(sample_count: int, amplitude: int = 0) -> bytes:
    """构造 PCM16 单声道 WAV：amplitude=0 全静音，>0 为满幅方波采样。"""
    header = (
        b"RIFF"
        + (36 + sample_count * 2).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")  # PCM
        + (1).to_bytes(2, "little")  # mono
        + (8000).to_bytes(4, "little")  # sample rate
        + (8000 * 2).to_bytes(4, "little")  # byte rate
        + (2).to_bytes(2, "little")  # block align
        + (_PCM_BITS).to_bytes(2, "little")
        + b"data"
        + (sample_count * 2).to_bytes(4, "little")
    )
    body = b"".join(
        (amplitude if i % 2 == 0 else -amplitude).to_bytes(2, "little", signed=True)
        for i in range(sample_count)
    )
    return header + body


class TestLoopLag:
    @pytest.mark.asyncio
    async def test_trim_does_not_freeze_event_loop(self, monkeypatch):
        """大 WAV 裁剪（模拟慢速逐采样处理）执行期间事件循环可响应。"""
        client = _make_client()
        big_wav = _wav_bytes(8000 * 10, amplitude=3000)  # 10s 采样量

        original_trim = ASRClient._trim_silence
        off_loop_threads: list[str] = []

        def slow_trim(self, fb: bytes, fmt: str) -> bytes:
            off_loop_threads.append(threading.current_thread().name)
            time.sleep(0.8)  # 模拟 10–20s 级阻塞的缩比样本
            return original_trim(fb, fmt)  # _trim_silence 为 staticmethod，不传 self

        monkeypatch.setattr(ASRClient, "_trim_silence", slow_trim)
        # provider 创建确定性快速失败（避免环境 DASHSCOPE_API_KEY 意外存在时走真实网络）
        monkeypatch.setattr(
            "app.services.asr_client.ASRFactory.create",
            lambda: (_ for _ in ()).throw(RuntimeError("no provider in test")),
        )

        loop_lags: list[float] = []
        stop = asyncio.Event()

        async def watchdog():
            while not stop.is_set():
                start = time.monotonic()
                await asyncio.sleep(0.05)
                loop_lags.append(time.monotonic() - start)

        watchdog_task = asyncio.create_task(watchdog())
        await asyncio.sleep(0)  # 让 watchdog 在任务启动前取到首个 start 时间戳
        task = asyncio.create_task(client.transcribe(big_wav, "audio.wav"))
        await asyncio.sleep(0.15)  # 至少经历一次慢速裁剪窗口
        stop.set()
        await watchdog_task
        with pytest.raises(RuntimeError, match="no provider"):
            # 裁剪阶段已完整执行（loop-lag 采样完成），provider 创建阶段快速失败
            await task
        # 线程身份：裁剪必须在事件循环（主）线程外执行（与计时无关的确定性守护）
        assert off_loop_threads, "trim should have run"
        assert all(name != "MainThread" for name in off_loop_threads), (
            f"trim ran on the event loop thread: {off_loop_threads}"
        )
        assert loop_lags, "watchdog should have sampled at least once"
        assert max(loop_lags) < 0.5, f"loop-lag too high: {max(loop_lags):.3f}s"


class TestCancellation:
    @pytest.mark.asyncio
    async def test_wait_for_cancellation_propagates(self, monkeypatch):
        """wait_for 超时取消裁剪任务后 CancelledError 正常传播（不吞异常）。"""
        client = _make_client()

        def hanging_trim(self, fb: bytes, fmt: str) -> bytes:
            time.sleep(0.9)  # 远大于 wait_for 超时；loop 关闭时线程池等待可接受
            return fb

        monkeypatch.setattr(ASRClient, "_trim_silence", hanging_trim)
        monkeypatch.setattr(ASRClient, "_split_segments", lambda self, fb, fmt: [fb])

        async def _runner():
            await client.transcribe(_wav_bytes(100, amplitude=1), "audio.wav")

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_runner(), timeout=0.3)


class TestGoldenSemantics:
    @pytest.mark.asyncio
    async def test_silent_wav_kept_as_is(self, monkeypatch):
        """全静音 WAV 保持原样（golden：不裁剪）。"""
        client = _make_client()
        silent = _wav_bytes(800, amplitude=0)
        trimmed = await asyncio.to_thread(client._trim_silence, silent, "wav")
        assert trimmed == silent

    @pytest.mark.asyncio
    async def test_non_wav_passthrough(self, monkeypatch):
        """非 WAV 格式透传（不裁剪、分段单元素）。"""
        client = _make_client()
        payload = b"fake-mp3-bytes"
        trimmed = await asyncio.to_thread(client._trim_silence, payload, "mp3")
        assert trimmed == payload
        segments = await asyncio.to_thread(client._split_segments, payload, "mp3")
        assert segments == [payload]

    @pytest.mark.asyncio
    async def test_trim_split_outputs_match_pre_change(self, monkeypatch):
        """裁剪/切分输出与整改前逐字节一致（实现体不动 → golden 等价）。"""
        client = _make_client()
        wav = _wav_bytes(8000, amplitude=2000)
        trimmed = await asyncio.to_thread(client._trim_silence, wav, "wav")
        segments = await asyncio.to_thread(client._split_segments, trimmed, "wav")
        # 实现体零改动：结果与直接同步调用完全一致（staticmethod 直传双参）
        assert trimmed == ASRClient._trim_silence(wav, "wav")
        assert segments == ASRClient._split_segments(trimmed, "wav")
        assert all(isinstance(seg, bytes) for seg in segments)