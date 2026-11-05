"""T49 / 2.2：mcp-gateway call_log_sink 落盘递归脱敏 单测。

验收判据：含手机号的嵌套 arguments 走一次审计写出，落盘 JSON 无 11 位明文
手机号（call_log_sink 落盘与 structlog 处理链统一经 common 递归脱敏覆盖）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


from app.call_log_sink import TempFileCallLogSink


def _audit_entry() -> dict:
    """构造 mcp-gateway 审计 entry 形态（嵌套 params 含 PII）。"""
    now = datetime.now(timezone.utc)
    return {
        "server": "crm",
        "tool": "crm.query",
        "params": {"arguments": {"phone": "13812341234", "kw": "账单"}},
        "trace_id": str(uuid.uuid4()),
        "session_id": "sess-1",
        "attempt": 1,
        "latency_ms": 12,
        "started_at": now,
        "ended_at": now,
        "status": "success",
        "error": None,
    }


class TestCallLogSinkMasking:
    def test_sink_write_masks_nested_phone(self, tmp_path: Path):
        """落盘 JSON：嵌套 params 中的 11 位手机号被掩码（无明文）。"""
        sink = TempFileCallLogSink(tmp_path / "call_log.jsonl")
        sink.write("mcp", _audit_entry())

        row = json.loads((tmp_path / "call_log.jsonl").read_text(encoding="utf-8"))
        assert row["channel"] == "mcp"
        assert "13812341234" not in json.dumps(row, ensure_ascii=False), (
            "落盘 JSON 不得含 11 位明文手机号"
        )
        assert row["params"]["arguments"]["phone"] == "138****1234"
        sink.close()

    def test_sink_write_multiple_lines_all_masked(self, tmp_path: Path):
        """多行落盘：逐行递归脱敏，全部无明文手机号。"""
        sink = TempFileCallLogSink(tmp_path / "call_log.jsonl")
        for _ in range(3):
            sink.write("mcp", _audit_entry())
        sink.close()

        text = (tmp_path / "call_log.jsonl").read_text(encoding="utf-8")
        assert len(text.strip().splitlines()) == 3
        assert "13812341234" not in text