"""evidence_case 证据采集-案件 Agent 节点。

遍历 case_ids，通过 MCP 拉取案件信息、历史工单、历史投诉，
写入新 CaseEvidenceBundle，由 merge_case_evidence reducer 合并。
MCP 超时/错误 → exception_info，不做业务判责。
"""
from __future__ import annotations

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
        try:
            case_info = await mcp.call(_SERVER, "query_case_info", {"case_id": case_id})
            history_tickets = await mcp.call(_SERVER, "query_history_tickets", {"case_id": case_id})
            history_complaints = await mcp.call(_SERVER, "query_history_complaints", {"case_id": case_id})
        except (MCPTimeoutError, MCPError) as e:
            logger.warning("evidence_case_mcp_error", case_id=case_id, error=str(e))
            return {
                "exception_info": ExceptionInfo(
                    error_type=type(e).__name__,
                    message=str(e),
                    node="evidence_case",
                ).model_dump()
            }

        case_evidence[case_id] = CaseEvidenceBundle(
            case_id=case_id,
            case_info=case_info if isinstance(case_info, dict) else {},
            history_tickets=history_tickets if isinstance(history_tickets, list) else [],
            history_complaints=history_complaints if isinstance(history_complaints, list) else [],
        )

    return {"case_evidence": case_evidence}
