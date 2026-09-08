"""human_review 人工断点节点。

使用 langgraph.types.interrupt 等待人工提交 review_action。
FATAL-1：无硬边到 sample_backflow；出边由 should_proceed_after_review 条件边路由。
"""
from __future__ import annotations

import structlog
from langgraph.types import interrupt

from app.models.state import (
    ComplaintSessionState,
    ReviewAction,
    TaskStatus,
)

logger = structlog.get_logger()

_ACTION_TO_STATUS = {
    ReviewAction.approve: TaskStatus.resolved,
    ReviewAction.reject_rejudge: TaskStatus.evidence_gathering,
    ReviewAction.escalate: TaskStatus.pending_senior_review,
    ReviewAction.modify_verdict: TaskStatus.resolved,
    ReviewAction.modify_response: TaskStatus.resolved,
    ReviewAction.add_evidence: TaskStatus.evidence_gathering,
}


async def human_review_node(state: ComplaintSessionState) -> dict:
    """人工复核断点：interrupt 等待人工输入，根据 review_action 更新状态。"""
    review_value = interrupt(
        {"prompt": "请人工复核判责结果与产出物", "session_id": state.session_id}
    )

    # 兼容 str 或 ReviewAction
    if isinstance(review_value, str):
        try:
            review_action = ReviewAction(review_value)
        except ValueError:
            review_action = ReviewAction.approve
    elif isinstance(review_value, ReviewAction):
        review_action = review_value
    elif isinstance(review_value, dict):
        ra = review_value.get("review_action")
        review_action = ReviewAction(ra) if ra else ReviewAction.approve
    else:
        review_action = ReviewAction.approve

    task_status = _ACTION_TO_STATUS.get(review_action, TaskStatus.pending_review)
    new_reject_count = state.reject_count + (
        1 if review_action == ReviewAction.reject_rejudge else 0
    )

    return {
        "human_review": {
            "review_action": review_action.value,
            "reject_count": new_reject_count,
        },
        "reject_count": new_reject_count,
        "task_status": task_status.value,
    }
