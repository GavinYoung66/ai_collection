"""call_qc.query_call_asr 内嵌实现（平移自 services/complaint-service/app/mcp/call_qc_bridge.py）。

仅平移 query_call_asr 相关：_resolve_session_id（快照反查通话 session_id）→
data-platform qc-source → ASR fixture 列表；含 map_qc_source_to_asr_fixture /
normalize_speaker。

call_qc 混合实现（design D1）：query_qc_violations / query_qc_alerts 不在本文件，
经 MCP 协议调 qc-service /mcp（见 app/qc_mcp_client.py）。

容错（spec mcp-real-path 4.2）：任何查不到或下游故障（404/5xx/超时/传输错）
一律返空列表并记可查日志，图继续执行、不裸返 500；仅对编程错误
（不支持的 tool、缺 case_id）抛 MCPError 快速失败。
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from app.bridge_http import BridgeHttpClient, BridgeNotFound
from app.mcp_errors import MCPError

logger = structlog.get_logger()

_SERVER = "call_qc"
TOOL_QUERY_CALL_ASR = "query_call_asr"


class CallQcAsrToolset:
    """query_call_asr：快照反查 session_id → data-platform qc-source → fixture 列表。"""

    def __init__(self, data_platform_url: str) -> None:
        self._dp = BridgeHttpClient(data_platform_url)

    async def call_tool(
        self,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        if tool != TOOL_QUERY_CALL_ASR:
            raise MCPError(_SERVER, tool, f"unsupported embedded call_qc tool: {tool}")

        case_id = str((params or {}).get("case_id") or "")
        if not case_id:
            raise MCPError(_SERVER, tool, f"{tool} requires case_id")

        call_session_id = await self._resolve_session_id(case_id, trace_id=trace_id)
        if not call_session_id:
            return []

        return await self._query_call_asr(call_session_id, trace_id=trace_id)

    async def _resolve_session_id(self, case_id: str, *, trace_id: str) -> Optional[str]:
        try:
            snap = await self._dp.request(
                "GET", f"/api/v1/data/snapshot/{case_id}", trace_id=trace_id
            )
        except BridgeNotFound:
            logger.info("call_qc_snapshot_miss", case_id=case_id, trace_id=trace_id)
            return None
        # Batch 3：快照 5xx/超时上抛由可靠性层分级；耗尽后 fallback 兜空列表
        if not isinstance(snap, dict):
            return None
        sid = str(snap.get("session_id") or "")
        return sid or None

    async def _query_call_asr(self, session_id: str, *, trace_id: str) -> list[dict[str, Any]]:
        try:
            source = await self._dp.request(
                "GET",
                f"/api/v1/data/sessions/{session_id}/qc-source",
                trace_id=trace_id,
            )
        except BridgeNotFound:
            logger.info("call_asr_qc_source_miss", session_id=session_id, trace_id=trace_id)
            return []
        # Batch 3：下游 5xx/超时上抛由可靠性层分级；耗尽后 fallback 兜空列表

        return map_qc_source_to_asr_fixture(source)


def normalize_speaker(raw: Any) -> str:
    """speaker 归一为 fixture 枚举：ai/human/agent → agent，user → user。

    data-platform map_speaker 已做一次归一；工具层再兜底，防止任何上游漏网
    的 ai/human 原值进入证据包（fixture 只认 agent/user）。
    """
    value = str(raw or "").strip().lower()
    if value == "user":
        return "user"
    return "agent"


def map_qc_source_to_asr_fixture(source: Any) -> list[dict[str, Any]]:
    """qc-source data → query_call_asr fixture（列表，单通话单元素）。

    - duration_seconds 缺失或 ≤0 不组装（fixture 要求 call_duration>0）
    - turns 无逐句时间戳：ts 给空串，禁止编造
    - speaker 工具层归一：ai/human→agent（不依赖上游一定已归一）
    """
    if not isinstance(source, dict):
        return []
    duration = source.get("duration_seconds")
    try:
        duration_i = int(duration) if duration is not None else 0
    except (TypeError, ValueError):
        duration_i = 0
    if duration_i <= 0:
        return []

    turns = source.get("turns") or []
    asr_items: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        asr_items.append(
            {
                "ts": "",
                "speaker": normalize_speaker(turn.get("speaker")),
                "text": str(turn.get("text") or ""),
            }
        )

    return [
        {
            "call_session_id": str(source.get("session_id") or ""),
            "call_start_time": source.get("started_at"),
            "call_duration": duration_i,
            "asr_text_with_timestamp": asr_items,
            "agent_role": "agent",
        }
    ]
