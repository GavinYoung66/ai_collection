"""call_qc 进程内桥（mcp-real-path / design D5）。

图节点（evidence_call_qc）仍只调 ``mcp.call("call_qc", tool, {"case_id": …})``。
质检服务不实现 call_qc 工具；本桥在投诉进程内：
- query_call_asr：快照反查 session_id → data-platform qc-source → fixture 列表
- query_qc_violations：快照反查 session_id → qc-service 台账明细 → fixture 列表

容错（spec mcp-real-path 4.2）：任何查不到或下游故障（404/5xx/超时/传输错）
一律返空列表并记可查日志，图继续执行、不裸返 500；仅对编程错误
（不支持的 tool、缺 case_id）抛 MCPError 快速失败。
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
import structlog

from app.mcp.bridge_http import BridgeHttpClient, BridgeNotFound
from app.mcp.errors import MCPError, MCPTimeoutError

logger = structlog.get_logger()

_SERVER = "call_qc"
_SUPPORTED_TOOLS = frozenset({"query_call_asr", "query_qc_violations"})


class CallQcBridge:
    """call_qc server 进程内真桥（data-platform + qc-service 两个下游）。"""

    def __init__(
        self,
        data_platform_url: str,
        qc_service_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # 同一注入 client 复用一个 MockTransport/连接池；两基座 base_url 不同
        self._dp = BridgeHttpClient(data_platform_url, client=client)
        self._qc = BridgeHttpClient(qc_service_url, client=client)

    async def call_tool(
        self,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        if tool not in _SUPPORTED_TOOLS:
            raise MCPError(_SERVER, tool, f"unsupported call_qc tool: {tool}")

        case_id = str((params or {}).get("case_id") or "")
        if not case_id:
            raise MCPError(_SERVER, tool, f"{tool} requires case_id")

        call_session_id = await self._resolve_session_id(case_id, trace_id=trace_id)
        if not call_session_id:
            return []

        if tool == "query_call_asr":
            return await self._query_call_asr(call_session_id, trace_id=trace_id)
        return await self._query_qc_violations(call_session_id, trace_id=trace_id)

    async def _resolve_session_id(self, case_id: str, *, trace_id: str) -> Optional[str]:
        try:
            snap = await self._dp.request(
                "GET", f"/api/v1/data/snapshot/{case_id}", trace_id=trace_id
            )
        except BridgeNotFound:
            logger.info("call_qc_snapshot_miss", case_id=case_id, trace_id=trace_id)
            return None
        except (MCPTimeoutError, MCPError) as e:
            # 4.2：data-platform 故障（5xx/超时）→ 无 session 可反查，返空不崩图
            logger.warning(
                "call_qc_snapshot_error", case_id=case_id, error=str(e), trace_id=trace_id
            )
            return None
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
        except (MCPTimeoutError, MCPError) as e:
            # 4.2：下游故障返空列表（默认值），图继续执行，不裸返 500
            logger.warning(
                "call_asr_qc_source_error", session_id=session_id, error=str(e), trace_id=trace_id
            )
            return []

        return map_qc_source_to_asr_fixture(source)

    async def _query_qc_violations(
        self, session_id: str, *, trace_id: str
    ) -> list[dict[str, Any]]:
        try:
            detail = await self._qc.request(
                "GET",
                f"/api/v1/qc/records/{session_id}",
                trace_id=trace_id,
            )
        except BridgeNotFound:
            logger.info("qc_violations_record_miss", session_id=session_id, trace_id=trace_id)
            return []
        except (MCPTimeoutError, MCPError) as e:
            # 4.2：qc-service 故障返空列表（默认值），图继续执行，不裸返 500
            logger.warning(
                "qc_violations_record_error", session_id=session_id, error=str(e), trace_id=trace_id
            )
            return []

        return map_qc_record_to_violations_fixture(detail)


def normalize_speaker(raw: Any) -> str:
    """speaker 归一为 fixture 枚举：ai/human/agent → agent，user → user。

    data-platform map_speaker 已做一次归一；桥层再兜底，防止任何上游漏网
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
    - speaker 桥层归一：ai/human→agent（不依赖上游一定已归一）
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


def map_qc_record_to_violations_fixture(detail: Any) -> list[dict[str, Any]]:
    """qc 台账明细 data → query_qc_violations fixture 列表（按 hits 逐条展开）。

    台账 API 无逐句时间戳/违规级 score：start_ts/end_ts 给空串、score 给 0.0，
    不伪造。
    """
    if not isinstance(detail, dict):
        return []
    session_id = str(detail.get("session_id") or "")
    qc_record_id = str(detail.get("qc_record_id") or "")
    hits = detail.get("hits") or []

    result: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        result.append(
            {
                "qc_id": qc_record_id,
                "violation_type": str(hit.get("redline_tag") or hit.get("rule_id") or ""),
                "violation_rule_id": str(hit.get("rule_id") or ""),
                "call_session_id": session_id,
                "start_ts": "",
                "end_ts": "",
                "snippet_text": str(hit.get("evidence_text") or ""),
                "score": 0.0,
            }
        )
    return result
