"""可插拔 call_log sink（Batch 4，任务 5.2）。

契约与 complaint-service ``app/observability/call_log_sink.py`` 完全同构
（``set_call_log_sink`` / ``emit_call_log(channel, entry)``，channel="mcp"）。
网关副本存在的理由：integration-boundaries 约束「Application code in one service
MUST NOT import another service's internal packages」——网关不得 import
complaint-service 内部包，故按同一契约在本网关内端口化，而非反向依赖。

默认无 sink 时 emit 为 no-op；注册 sink（如 TempFileCallLogSink）后额外写出，
每行一条 JSON（首字段为 channel）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class CallLogSink(Protocol):
    def write(self, channel: str, entry: dict[str, Any]) -> None:
        """channel: ``mcp`` | ``llm``。"""


class NullCallLogSink:
    def write(self, channel: str, entry: dict[str, Any]) -> None:  # noqa: ARG002
        return None


class TempFileCallLogSink:
    """最小 file sink：每行一条 JSON（含 channel），供审计排障/验收落盘。

    持久持有 append 句柄 + 每行 flush，避免高频调用下每行 open/close。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, channel: str, entry: dict[str, Any]) -> None:
        row = {"channel": channel, **_jsonable(entry)}
        self._fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()


_sink: Optional[CallLogSink] = None


def set_call_log_sink(sink: CallLogSink | None) -> None:
    """注册全局 sink；传 None 恢复默认（emit no-op）。"""
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
