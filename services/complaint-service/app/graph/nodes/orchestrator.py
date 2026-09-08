"""orchestrator 调度 Agent 节点。

弱中心化、纯代码条件分支，不调用 LLM 做业务推理。
- 初始化 task_status=evidence_gathering
- 异常/Token 超限 → pending_review
- 可 MCP 读工单基础信息（本批不强制实际调用）
"""
from __future__ import annotations

from app.models.state import ComplaintSessionState, TaskStatus


async def orchestrator_node(state: ComplaintSessionState) -> dict:
    """调度节点：初始化任务状态，异常或 Token 超限转人工。"""
    # 异常或 Token 超限 → 待人工复核
    if state.exception_info is not None or state.total_tokens_consumed >= state.max_token_threshold:
        return {"task_status": TaskStatus.pending_review.value}

    # 正常流程：初始化证据采集
    return {"task_status": TaskStatus.evidence_gathering.value}
