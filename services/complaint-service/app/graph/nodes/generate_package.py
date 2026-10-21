"""generate_package 产出物生成 Agent 节点。

基于判责草稿与证据生成完整研判数据包，全部附带 RAG 来源 ID。
MODERATE-5：MCP 模板检索异常时降级为模板填充。
批次7：产出后调用 L4 postcheck（design D7）。
LLM 降级（spec graph-nodes）：超时/熔断/整链失败 → 规则兜底数据包 + pending_review。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_llm_provider, get_mcp_client
from app.llm.provider import CircuitBreakerOpenError, LLMCallError
from app.mcp.errors import MCPError, MCPTimeoutError
from app.models.state import (
    ComplaintSessionState,
    ExceptionInfo,
    JudgmentPackage,
    TaskStatus,
)
from app.prompt.loader import load_prompt
from app.service.degrade_llm import node_degrade_fallback
from app.service.evidence_refs import extract_doc_ids_from_hits
from app.service.guardrails_postcheck import postcheck
from app.service.security import mask_before_persist

logger = structlog.get_logger()

_RAG_SERVER = "rag_kb"


def _extract_doc_ids(rag_result: list) -> list[str]:
    return extract_doc_ids_from_hits(rag_result)


async def generate_package_node(state: ComplaintSessionState) -> dict:
    """产出物生成节点：MCP 检索答辩模板 + LLM 产出 JudgmentPackage。"""
    # 防御性短路（与 should_proceed_after_verdict 双保险）
    if state.verdict_draft is None or state.task_status == TaskStatus.pending_review:
        logger.warning(
            "generate_package_short_circuit",
            has_verdict=state.verdict_draft is not None,
            task_status=getattr(state.task_status, "value", state.task_status),
        )
        return {}

    mcp = get_mcp_client()
    exception_info = None
    rag_result: list = []

    # MODERATE-5：MCP try/except
    try:
        rag_result = await mcp.call(
            _RAG_SERVER, "search_response_templates", {"query": state.complaint_text[:500]},
            trace_id=state.trace_id, session_id=state.session_id,
        )
        if not isinstance(rag_result, list):
            rag_result = [rag_result] if rag_result else []
    except (MCPTimeoutError, MCPError) as e:
        logger.warning("generate_package_rag_error", error=str(e))
        rag_result = []
        exception_info = ExceptionInfo(
            error_type=type(e).__name__, message=str(e), node="generate_package"
        ).model_dump()

    prompt = load_prompt(
        "complaint_generate_package",
        verdict_draft=mask_before_persist(str(state.verdict_draft.model_dump())[:2000]),
        rag_templates=mask_before_persist(str(rag_result)[:1000]),
        complaint_text=mask_before_persist(state.complaint_text[:1500]),
    )
    llm = get_llm_provider()
    try:
        package: JudgmentPackage = await llm.invoke(
            prompt,
            schema=JudgmentPackage,
            trace_id=state.trace_id,
            session_id=state.session_id,
            agent_role="generate_package",
        )
    except (TimeoutError, CircuitBreakerOpenError, LLMCallError) as e:
        # LLM 降级（spec graph-nodes）：规则兜底数据包 + pending_review + A5017 事件，
        # 降级路径跳过 L4 postcheck（无 LLM 产物可校验）
        return await node_degrade_fallback("generate_package", state, e)

    # L4 后置校验（design D7 挂钩）
    rag_doc_ids = _extract_doc_ids(rag_result)
    pkg_dict = package.model_dump(mode="json") if hasattr(package, "model_dump") else dict(package)
    # JudgmentPackage 无 verdict 字段；注入判责草稿，避免 L4 误判失败→兜底短文
    if state.verdict_draft is not None:
        vd = state.verdict_draft
        pkg_dict.setdefault(
            "verdict",
            vd.verdict.value if hasattr(vd.verdict, "value") else str(vd.verdict),
        )
        pkg_dict.setdefault("confidence", getattr(vd, "confidence", 0.0))
    rag_text = " ".join(str(x) for x in rag_result)[:2000]
    # 把投诉原文与证据摘要并入比对文本，避免金额/天数被误判为幻觉
    try:
        from app.utils.evidence_helper import _collect_all_evidence

        evidence_blob = str(_collect_all_evidence(state.model_dump()))[:2500]
    except Exception:
        evidence_blob = ""
    rag_text = f"{rag_text}\n{state.complaint_text}\n{evidence_blob}"[:5000]
    _ok, sanitized = postcheck(pkg_dict, rag_doc_ids=rag_doc_ids, rag_text=rag_text)
    try:
        fields = set(JudgmentPackage.model_fields.keys())
        package = JudgmentPackage(**{k: v for k, v in sanitized.items() if k in fields})
    except Exception:
        logger.warning("generate_package_postcheck_rebuild_failed", ok=_ok)

    result: dict = {
        "judgment_package": package,
        "rag_hit_doc_ids": rag_doc_ids,
    }
    if sanitized.get("stop_call_flag"):
        result["stop_call_flag"] = True
    if exception_info is not None:
        result["exception_info"] = exception_info
    return result
