"""evidence_case 证据采集-案件 Agent 节点。

遍历 case_ids，通过 MCP 拉取案件信息、历史工单、历史投诉，
写入新 CaseEvidenceBundle，由 merge_case_evidence reducer 合并。
MCP 超时/错误 → exception_info，不做业务判责。
"""
from __future__ import annotations

import asyncio

import structlog

from app.api.deps import get_mcp_client
from app.mcp.errors import MCPError, MCPTimeoutError
from app.models.state import (
    CaseEvidenceBundle,
    ComplaintSessionState,
    ExceptionInfo,
)

logger = structlog.get_logger()

_SERVER = "business_ticket"


async def evidence_case_node(state: ComplaintSessionState) -> dict:
    """案件证据采集节点。"""
    mcp = get_mcp_client()
    case_evidence: dict[str, CaseEvidenceBundle] = {}

    for case_id in state.case_ids:
        # 单 case 内三查询并发（return_exceptions=True 收集，不吞异常）。
        # 注意：失败路径下其余查询仍会发出并收集全部异常（spec 要求
        # "其余查询的异常不被掩盖为成功"），与串行短路行为不同属预期设计。
        case_info, history_tickets, history_complaints = await asyncio.gather(
            mcp.call(
                _SERVER, "query_case_info", {"case_id": case_id},
                trace_id=state.trace_id, session_id=state.session_id,
            ),
            mcp.call(
                _SERVER, "query_history_tickets", {"case_id": case_id},
                trace_id=state.trace_id, session_id=state.session_id,
            ),
            mcp.call(
                _SERVER, "query_history_complaints", {"case_id": case_id},
                trace_id=state.trace_id, session_id=state.session_id,
            ),
            return_exceptions=True,
        )

        # 任一 MCP 异常即整节点降级，语义与串行版本逐字对齐
        for maybe_exc in (case_info, history_tickets, history_complaints):
            if isinstance(maybe_exc, (MCPTimeoutError, MCPError)):
                logger.warning(
                    "evidence_case_mcp_error", case_id=case_id,
                    error_type=type(maybe_exc).__name__, error=str(maybe_exc),
                )
                return {
                    "exception_info": ExceptionInfo(
                        error_type=type(maybe_exc).__name__,
                        message=str(maybe_exc),
                        node="evidence_case",
                    ).model_dump()
                }

        # 非 MCP 异常与串行行为一致：直接抛出（不做降级）
        for maybe_exc in (case_info, history_tickets, history_complaints):
            if isinstance(maybe_exc, BaseException):
                raise maybe_exc

        case_evidence[case_id] = CaseEvidenceBundle(
            case_id=case_id,
            case_info=case_info if isinstance(case_info, dict) else {},
            history_tickets=history_tickets if isinstance(history_tickets, list) else [],
            history_complaints=history_complaints if isinstance(history_complaints, list) else [],
        )

    return {"case_evidence": case_evidence}
