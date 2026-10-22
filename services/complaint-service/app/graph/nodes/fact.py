"""fact 事实还原 Agent 节点。

基于证据调用 LLM 输出事实时序，禁止违规判定与责任结论。
LLM 降级（spec graph-nodes）：超时/熔断/整链失败 → 规则兜底时序 + pending_review。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_llm_provider
from app.llm.provider import CircuitBreakerOpenError, LLMCallError
from app.models.state import ComplaintSessionState, FactTimeline
from common.prompt.loader import load_prompt
from app.service.degrade_llm import node_degrade_fallback
from app.utils.evidence_helper import _collect_all_evidence

logger = structlog.get_logger()


async def fact_node(state: ComplaintSessionState) -> dict:
    """事实还原节点：产出 FactTimeline，不输出责任结论。"""
    evidence = _collect_all_evidence(state.model_dump())
    prompt = load_prompt("complaint_fact_timeline", service="complaint-service", evidence_text=str(evidence)[:4000])
    llm = get_llm_provider()
    try:
        timeline: FactTimeline = await llm.invoke(
            prompt,
            schema=FactTimeline,
            trace_id=state.trace_id,
            session_id=state.session_id,
            agent_role="fact",
        )
    except (TimeoutError, CircuitBreakerOpenError, LLMCallError) as e:
        # LLM 降级：规则兜底草稿 + pending_review，不抛（spec graph-nodes）
        return await node_degrade_fallback("fact", state, e)
    return {"fact_timeline": timeline}
