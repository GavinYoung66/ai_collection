"""T11：ASR 分段读取使用 Path.read_bytes，确定性关闭句柄，不泄漏 fd。"""
from __future__ import annotations

import builtins
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.asr_providers.dashscope_provider import DashScopeASRProvider


def _fake_ffmpeg_run(cmd, **_kwargs):
    """模拟 ffmpeg segment：按输出 pattern 生成 3 个片段文件。"""
    pattern = cmd[-1]
    for i in range(3):
        Path(pattern.replace("%03d", f"{i:03d}")).write_bytes(f"seg-{i}".encode())
    return MagicMock(returncode=0)


def _prepare(monkeypatch, tmp_path, *, run=None, which="ffmpeg"):
    seg_dir = tmp_path / "segments"
    seg_dir.mkdir()
    monkeypatch.setattr(
        "app.services.asr_providers.dashscope_provider.shutil.which",
        lambda _name: which,
    )
    monkeypatch.setattr(
        "app.services.asr_providers.dashscope_provider.tempfile.mkdtemp",
        lambda **_kwargs: str(seg_dir),
    )
    monkeypatch.setattr(
        "app.services.asr_providers.dashscope_provider.subprocess.run",
        run or _fake_ffmpeg_run,
    )
    return seg_dir


def test_segment_audio_returns_all_bytes_in_order(monkeypatch, tmp_path):
    """正常流程：3 个片段按时间顺序返回，临时目录清理。"""
    seg_dir = _prepare(monkeypatch, tmp_path)

    segments = DashScopeASRProvider._segment_audio(b"audio", "wav")

    assert segments == [b"seg-0", b"seg-1", b"seg-2"]
    assert not seg_dir.exists(), "临时分段目录必须清理"


def test_segment_audio_does_not_use_unclosed_binary_open(monkeypatch, tmp_path):
    """资源句柄：分段读取不得走未关闭的 ``open(p, 'rb').read()``。"""
    _prepare(monkeypatch, tmp_path)
    real_open = builtins.open
    read_opens: list[tuple[object, str]] = []

    def spy_open(file, mode="r", *args, **kwargs):
        if "r" in mode:
            read_opens.append((file, mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)

    segments = DashScopeASRProvider._segment_audio(b"audio", "wav")

    assert len(segments) == 3
    assert read_opens == [], f"分段读取仍使用裸 open: {read_opens}"


def test_segment_audio_fd_count_stays_flat_across_batches(monkeypatch, tmp_path):
    """批量边界：连续处理 100 段后进程 fd 数不线性增长。"""
    _prepare(monkeypatch, tmp_path)
    # 保留分段目录跨批次复用：真实实现每批 rmtree 后重建，这里屏蔽清理
    # 以隔离「fd 水位」这一被测变量。
    monkeypatch.setattr(
        "app.services.asr_providers.dashscope_provider.shutil.rmtree",
        lambda *_a, **_k: None,
    )
    fd_dir = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    if not os.path.isdir(fd_dir):
        pytest.skip("当前平台不提供 fd 目录，跳过 fd 水位断言")

    before = len(os.listdir(fd_dir))
    for _ in range(100):
        DashScopeASRProvider._segment_audio(b"audio", "wav")
    after = len(os.listdir(fd_dir))

    assert after - before <= 5, f"fd 数从 {before} 增长到 {after}"


def test_segment_audio_without_ffmpeg_raises(monkeypatch, tmp_path):
    """异常：ffmpeg 不可用时明确报错。"""
    _prepare(monkeypatch, tmp_path, which=None)
    with pytest.raises(RuntimeError, match="ffmpeg 不可用"):
        DashScopeASRProvider._segment_audio(b"audio", "wav")


def test_segment_audio_with_no_output_raises(monkeypatch, tmp_path):
    """边界：ffmpeg 成功但未产生片段 → 明确报错，不返回空列表。"""
    _prepare(monkeypatch, tmp_path, run=lambda *_a, **_k: MagicMock(returncode=0))
    with pytest.raises(RuntimeError, match="未产生任何片段"):
        DashScopeASRProvider._segment_audio(b"audio", "wav")
