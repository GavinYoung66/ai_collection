"""compliance 合规判定 Agent 节点。

先 MCP 检索法规（异常降级为 rag_result=[] + exception_info），
再调用 LLM 输出合规意见，禁止最终定责。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_llm_provider, get_mcp_client
from app.mcp.errors import MCPError, MCPTimeoutError
from app.models.state import (
    ComplianceOpinion,
    ComplaintSessionState,
    ExceptionInfo,
)
from app.prompt.loader import load_prompt
from app.service.evidence_refs import extract_doc_ids_from_hits
from app.utils.evidence_helper import _collect_all_evidence

logger = structlog.get_logger()

_RAG_SERVER = "rag_kb"


async def compliance_node(state: ComplaintSessionState) -> dict:
    """合规判定节点：MCP 检索法规 + LLM 产出 ComplianceOpinion。"""
    mcp = get_mcp_client()
    exception_info = None
    rag_result: list = []

    try:
        rag_result = await mcp.call(
            _RAG_SERVER, "search_regulations", {"query": state.complaint_text[:500]}
        )
        if not isinstance(rag_result, list):
            rag_result = [rag_result] if rag_result else []
    except (MCPTimeoutError, MCPError) as e:
        logger.warning("compliance_rag_error", error=str(e))
        rag_result = []
        exception_info = ExceptionInfo(
            error_type=type(e).__name__, message=str(e), node="compliance"
        ).model_dump()

    rag_hit_doc_ids = extract_doc_ids_from_hits(rag_result)

    evidence = _collect_all_evidence(state.model_dump())
    prompt = load_prompt(
        "complaint_compliance",
        evidence_text=str(evidence)[:3500],
        complaint_text=state.complaint_text[:1500],
        rag_refs=str(rag_result)[:1000],
    )
    llm = get_llm_provider()
    opinion: ComplianceOpinion = await llm.invoke(
        prompt,
        schema=ComplianceOpinion,
        trace_id=state.trace_id,
        session_id=state.session_id,
        agent_role="compliance",
    )

    result = {"compliance_opinion": opinion, "rag_hit_doc_ids": rag_hit_doc_ids}
    if exception_info is not None:
        result["exception_info"] = exception_info
    return result
