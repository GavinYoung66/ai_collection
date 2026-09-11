"""call_qc 工具清单。qc 只实现违规/告警查询，不实现 ASR 与会话主数据。"""
from __future__ import annotations

from typing import Any

TOOL_QUERY_QC_VIOLATIONS = "query_qc_violations"
TOOL_QUERY_QC_ALERTS = "query_qc_alerts"
FORBIDDEN_TOOLS = frozenset({"query_call_asr", "query_call_sessions"})

TOOL_PARAMS = ("case_id", "session_id")

TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": TOOL_QUERY_QC_VIOLATIONS,
        "server": "call_qc",
        "params": list(TOOL_PARAMS),
        "description": "查询质检违规明细",
    },
    {
        "name": TOOL_QUERY_QC_ALERTS,
        "server": "call_qc",
        "params": list(TOOL_PARAMS),
        "description": "查询质检高危告警",
    },
)


def implemented_tool_names() -> tuple[str, ...]:
    return tuple(item["name"] for item in TOOL_SPECS)
