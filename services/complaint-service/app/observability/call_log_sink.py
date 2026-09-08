"""可插拔 call_log sink（ENG-01 stretch / Batch-D 最小切片）。

默认仍为进程内 list；注册 sink 后额外写出。
**不等于** ENG-02 Metrics / ENG-07 safe_message / COMP-01/02 完成。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class CallLogSink(Protocol):
    def write(self, channel: str, entry: dict[str, Any]) -> None:
        """channel: ``mcp`` | ``llm``."""


class NullCallLogSink:
    def write(self, channel: str, entry: dict[str, Any]) -> None:
        return None


class TempFileCallLogSink:
    """最小 tempfile/file sink：每行一条 JSON（含 channel）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def write(self, channel: str, entry: dict[str, Any]) -> None:
        row = {"channel": channel, **_jsonable(entry)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


_sink: Optional[CallLogSink] = None


def set_call_log_sink(sink: CallLogSink | None) -> None:
    """注册全局 sink；传 None 恢复默认（仅内存 log）。"""
    global _sink
    _sink = sink


def get_call_log_sink() -> CallLogSink | None:
    return _sink


def emit_call_log(channel: str, entry: dict[str, Any]) -> None:
    sink = _sink
    if sink is None:
        return
    sink.write(channel, entry)


def _jsonable(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in entry.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
