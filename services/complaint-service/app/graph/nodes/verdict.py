"""verdict 综合判责 Agent 节点。

融合 fact_timeline / compliance_opinion / user_demand 三方输出，
输出判责草稿（含逐案判责）。仅草稿，无最终定责权限。

MODERATE-6：前置节点输出缺失时转人工，避免 None 传入 LLM。
LLM 降级（spec graph-nodes）：超时/熔断/整链失败 → 规则兜底草稿 + pending_review。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_llm_provider
from app.llm.provider import CircuitBreakerOpenError, LLMCallError
from app.models.state import (
    ComplaintSessionState,
    ExceptionInfo,
    TaskStatus,
    VerdictDraft,
)
from common.prompt.loader import load_prompt
from app.service.degrade_llm import node_degrade_fallback

logger = structlog.get_logger()


async def verdict_node(state: ComplaintSessionState) -> dict:
    """综合判责节点：None 防御 + LLM 产出 VerdictDraft + 降级规则兜底。"""
    # MODERATE-6：前置输出缺失 → 转人工
    if state.fact_timeline is None or state.compliance_opinion is None or state.user_demand is None:
        logger.warning("verdict_missing_inputs")
        return {
            "exception_info": ExceptionInfo(
                error_type="llm_degraded",
                message="fact_timeline/compliance_opinion/user_demand 缺失",
                node="verdict",
            ).model_dump(),
            "task_status": TaskStatus.pending_review.value,
        }

    prompt = load_prompt(
        "complaint_verdict",
        service="complaint-service",
        fact_timeline=str(state.fact_timeline.model_dump())[:3000],
        compliance_opinion=str(state.compliance_opinion.model_dump())[:2000],
        user_demand=str(state.user_demand.model_dump())[:1000],
        complaint_text=state.complaint_text[:1500],
    )
    llm = get_llm_provider()
    try:
        draft: VerdictDraft = await llm.invoke(
            prompt,
            schema=VerdictDraft,
            trace_id=state.trace_id,
            session_id=state.session_id,
            agent_role="verdict",
        )
    except (TimeoutError, CircuitBreakerOpenError, LLMCallError) as e:
        # LLM 降级：规则兜底草稿 + pending_review，不抛（spec graph-nodes）
        return await node_degrade_fallback("verdict", state, e)

    # confidence < 0.7 自动标记冲突
    if draft.confidence < 0.7:
        draft.conflict_highlight = True

    return {"verdict_draft": draft}
