"""T17：call_log sink 句柄生命周期单测。

覆盖：显式 close 幂等、替换 sink 先关闭旧句柄、__del__ 兜底、关闭后写入报错、
无 close 的 sink 兼容、close 抛错不阻断替换。
"""
from __future__ import annotations

import gc
import os

import pytest

from app.call_log_sink import (
    NullCallLogSink,
    TempFileCallLogSink,
    emit_call_log,
    get_call_log_sink,
    set_call_log_sink,
)


def _fd_count() -> int | None:
    for path in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(path))
        except OSError:
            continue
    return None


@pytest.fixture(autouse=True)
def _reset_sink():
    set_call_log_sink(None)
    yield
    set_call_log_sink(None)


def test_write_then_close_flushes_and_releases_handle(tmp_path):
    path = tmp_path / "mcp_calls.jsonl"
    sink = TempFileCallLogSink(path)
    set_call_log_sink(sink)
    emit_call_log("mcp", {"tool": "search_regulations", "latency_ms": 3})

    assert path.read_text(encoding="utf-8").strip()
    sink.close()
    assert sink._fh is None
    assert get_call_log_sink() is sink


def test_close_is_idempotent(tmp_path):
    sink = TempFileCallLogSink(tmp_path / "a.jsonl")
    sink.close()
    sink.close()
    assert sink._fh is None


def test_write_after_close_raises(tmp_path):
    sink = TempFileCallLogSink(tmp_path / "a.jsonl")
    sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        sink.write("mcp", {"tool": "t"})


def test_replacing_sink_closes_old_handle(tmp_path):
    old_path = tmp_path / "old.jsonl"
    new_path = tmp_path / "new.jsonl"
    old_sink = TempFileCallLogSink(old_path)
    set_call_log_sink(old_sink)
    emit_call_log("mcp", {"tool": "old"})

    new_sink = TempFileCallLogSink(new_path)
    set_call_log_sink(new_sink)

    assert old_sink._fh is None, "替换后旧句柄必须关闭"
    assert new_sink._fh is not None
    assert get_call_log_sink() is new_sink
    assert "old" in old_path.read_text(encoding="utf-8")


def test_set_none_closes_current_sink(tmp_path):
    sink = TempFileCallLogSink(tmp_path / "a.jsonl")
    set_call_log_sink(sink)
    set_call_log_sink(None)
    assert sink._fh is None
    assert get_call_log_sink() is None


def test_reassigning_same_instance_keeps_handle(tmp_path):
    sink = TempFileCallLogSink(tmp_path / "a.jsonl")
    set_call_log_sink(sink)
    set_call_log_sink(sink)
    assert sink._fh is not None


def test_sink_without_close_is_tolerated():
    class WriteOnlySink:
        def write(self, channel, entry):
            return None

    set_call_log_sink(WriteOnlySink())
    set_call_log_sink(NullCallLogSink())  # 不应因旧 sink 无 close 而报错
    assert isinstance(get_call_log_sink(), NullCallLogSink)


def test_close_failure_does_not_block_replacement(tmp_path):
    class BrokenCloseSink:
        def write(self, channel, entry):
            return None

        def close(self):
            raise RuntimeError("close failed")

    set_call_log_sink(BrokenCloseSink())
    new_sink = TempFileCallLogSink(tmp_path / "new.jsonl")
    set_call_log_sink(new_sink)
    assert get_call_log_sink() is new_sink


def test_del_closes_handle(tmp_path):
    sink = TempFileCallLogSink(tmp_path / "a.jsonl")
    handle = sink._fh
    del sink
    gc.collect()
    assert handle.closed


@pytest.mark.skipif(_fd_count() is None, reason="平台无 /proc 或 /dev/fd")
def test_repeated_create_close_does_not_leak_fds(tmp_path):
    before = _fd_count()
    for i in range(100):
        sink = TempFileCallLogSink(tmp_path / f"f{i}.jsonl")
        sink.write("mcp", {"i": i})
        sink.close()
    after = _fd_count()
    assert after - before <= 5, f"fd 泄漏：{before} → {after}"
