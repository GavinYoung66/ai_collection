"""user_demand 用户诉求 Agent 节点。

调用 LLM 输出用户诉求分析（诉求类型/情绪等级/二次投诉风险），禁止责任判定。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_llm_provider
from app.models.state import ComplaintSessionState, UserDemandAnalysis
from app.prompt.loader import load_prompt

logger = structlog.get_logger()


async def user_demand_node(state: ComplaintSessionState) -> dict:
    """用户诉求节点：产出 UserDemandAnalysis，不做责任判定。"""
    prompt = load_prompt("complaint_user_demand", complaint_text=state.complaint_text[:2000])
    llm = get_llm_provider()
    demand: UserDemandAnalysis = await llm.invoke(
        prompt,
        schema=UserDemandAnalysis,
        trace_id=state.trace_id,
        session_id=state.session_id,
        agent_role="user_demand",
    )
    return {"user_demand": demand}
