"""网关 → qc-service /mcp 的 MCP client（streamable-http）。

call_qc 混合实现（design D1）：query_qc_violations / query_qc_alerts 由本网关
经 MCP 协议调 qc-service MCP server endpoint（任务 3.2/3.3）。

- 每次调用新建短连接（stateless_http 服务端无需会话粘滞；Batch 3 再评估连接池）
- Batch 3：下游 5xx/超时/传输错/isError 抛 MCPError，由可靠性层分级重试；
  重试耗尽后路由 fallback 兜空列表（spec 4.2 容错：图继续判，不抛异常转人工）
- 返回形状与 qc-service CallQcServer 输出一致（含 qc_record_id 等字段），
  不在网关层重排
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.bridge_http import BridgeHttpClient, BridgeNotFound
from app.config import settings
from app.mcp_errors import MCPError, MCPTimeoutError

logger = structlog.get_logger()

_REMOTE_TOOLS = frozenset({"query_qc_violations", "query_qc_alerts"})
_CALL_TIMEOUT = 10.0

# 进程内共享 data-platform 快照客户端：_resolve_session_id 复用连接池，避免每调用新建 client
_dp_client: BridgeHttpClient | None = None


def _get_dp_client() -> BridgeHttpClient:
    global _dp_client
    if _dp_client is None:
        _dp_client = BridgeHttpClient(settings.DATA_PLATFORM_URL)
    return _dp_client


async def call_qc_remote(
    tool: str,
    params: dict,
    *,
    trace_id: str = "",
) -> list[dict[str, Any]]:
    """经 MCP 协议调 qc-service /mcp 的 call_qc 工具。

    - 调用方未带 session_id 时经 data-platform 快照反查（对齐源桥 _resolve_session_id，
      qc-service CallQcServer 需 case_id + session_id 双参才能定位台账）
    - query_qc_violations 返回前重映射为 complaint-service fixture 形状
      （spec mcp-real-path：每个工具返回值 SHALL 对齐现有 fixture 形状）
    - Batch 3：下游故障/超时/isError 抛 MCPError（业务可重试错），
      容错空列表由可靠性层 fallback 兜底
    """
    if tool not in _REMOTE_TOOLS:
        raise ValueError(f"unsupported remote call_qc tool: {tool}")

    params = dict(params or {})
    case_id = str(params.get("case_id") or "")
    session_id = str(params.get("session_id") or "")
    if not session_id and case_id:
        session_id = await _resolve_session_id(case_id, trace_id=trace_id)
    params["session_id"] = session_id

    url = f"{settings.QC_SERVICE_URL.rstrip('/')}/mcp"
    try:
        # mcp 1.30 streamable_http_client 不收 timeout 参数：经注入 http_client 控制超时
        async with httpx.AsyncClient(timeout=_CALL_TIMEOUT) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, params)
    except Exception as e:
        # Batch 3：下游故障（连接拒绝/5xx/超时/协议错）上抛，可靠性层分级重试
        logger.warning(
            "call_qc_remote_error",
            tool=tool,
            url=url,
            error=str(e),
            trace_id=trace_id,
        )
        raise MCPError("call_qc", tool, f"qc mcp call failed: {e}") from e

    if result.isError:
        logger.warning(
            "call_qc_remote_tool_error",
            tool=tool,
            error=str(result.content)[:300],
            trace_id=trace_id,
        )
        raise MCPError("call_qc", tool, f"qc tool error: {str(result.content)[:200]}")

    rows = _parse_result(result)
    if tool == "query_qc_violations":
        return _map_violations_to_fixture(rows, session_id=session_id)
    return rows


async def _resolve_session_id(case_id: str, *, trace_id: str) -> str:
    """快照反查通话 session_id（对齐源桥 call_qc_bridge._resolve_session_id）。

    data-platform 查无（404）时返空串；qc-service 收到空 session_id 会自行返空列表，
    图继续判不崩（spec 4.2）。Batch 3：快照 5xx/超时上抛由可靠性层分级。
    """
    try:
        dp = _get_dp_client()
        snap = await dp.request(
            "GET", f"/api/v1/data/snapshot/{case_id}", trace_id=trace_id
        )
    except BridgeNotFound:
        logger.info("qc_remote_snapshot_miss", case_id=case_id, trace_id=trace_id)
        return ""
    except (MCPTimeoutError, MCPError) as e:
        logger.warning(
            "qc_remote_snapshot_error", case_id=case_id, error=str(e), trace_id=trace_id
        )
        raise
    if not isinstance(snap, dict):
        return ""
    return str(snap.get("session_id") or "")


def _map_violations_to_fixture(
    rows: list[dict[str, Any]], *, session_id: str = ""
) -> list[dict[str, Any]]:
    """qc-service 违规形状 → complaint-service fixture 形状。

    qc-service 产出 {qc_record_id, redline_tag, violation_rule_id, snippet_text, ts}
    重映射为 fixture {qc_id, violation_type, violation_rule_id, call_session_id,
    start_ts, end_ts, snippet_text, score}。qc-service 无逐句起止时刻/违规级 score：
    start_ts/end_ts 给空串、score 给 0.0（对齐源桥 map_qc_record_to_violations_fixture，
    不伪造）。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "qc_id": str(row.get("qc_record_id") or ""),
                "violation_type": str(row.get("redline_tag") or row.get("violation_rule_id") or ""),
                "violation_rule_id": str(row.get("violation_rule_id") or ""),
                "call_session_id": session_id,
                "start_ts": "",
                "end_ts": "",
                "snippet_text": str(row.get("snippet_text") or ""),
                "score": 0.0,
            }
        )
    return out


def _parse_result(result: Any) -> list[dict[str, Any]]:
    """从 CallToolResult 提取 list[dict]：优先 structuredContent，兜底 text JSON。"""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP 对非 dict 返回值的包装：{"result": [...]}
        payload = structured.get("result", structured)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    return []
