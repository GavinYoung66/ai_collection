"""user_demand 用户诉求 Agent 节点。

调用 LLM 输出用户诉求分析（诉求类型/情绪等级/二次投诉风险），禁止责任判定。
LLM 降级（spec graph-nodes）：超时/熔断/整链失败 → 规则兜底分析 + pending_review。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_llm_provider
from app.llm.provider import CircuitBreakerOpenError, LLMCallError
from app.models.state import ComplaintSessionState, UserDemandAnalysis
from common.prompt.loader import load_prompt
from app.service.degrade_llm import node_degrade_fallback

logger = structlog.get_logger()


async def user_demand_node(state: ComplaintSessionState) -> dict:
    """用户诉求节点：产出 UserDemandAnalysis，不做责任判定。"""
    prompt = load_prompt("complaint_user_demand", service="complaint-service", complaint_text=state.complaint_text[:2000])
    llm = get_llm_provider()
    try:
        demand: UserDemandAnalysis = await llm.invoke(
            prompt,
            schema=UserDemandAnalysis,
            trace_id=state.trace_id,
            session_id=state.session_id,
            agent_role="user_demand",
        )
    except (TimeoutError, CircuitBreakerOpenError, LLMCallError) as e:
        # LLM 降级：规则兜底草稿 + pending_review，不抛（spec graph-nodes）
        return await node_degrade_fallback("user_demand", state, e)
    return {"user_demand": demand}
