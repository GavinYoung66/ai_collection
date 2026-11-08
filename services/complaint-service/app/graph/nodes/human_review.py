"""human_review 人工断点节点。

使用 langgraph.types.interrupt 等待人工提交 review_action。
FATAL-1：无硬边到 sample_backflow；出边由 should_proceed_after_review 条件边路由。

T114（spec graph-nodes「人工审阅恢复的 fail-safe 判定」）：恢复值 MUST 先经严格
校验——非法、越界或恶意构造的值与缺失/空值一律落 pending_review + exception_info
并告警，MUST NOT 被解释为 approve、MUST NOT 触发工单自动结案。
"""
from __future__ import annotations

import structlog
from langgraph.types import interrupt

from app.models.state import (
    ComplaintSessionState,
    ExceptionInfo,
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

# T114：非法/缺失恢复值落 exception_info 的 error_type 值
_ERROR_TYPE_INVALID_REVIEW_ACTION = "invalid_review_action"


def _parse_review_action(review_value: object) -> tuple[ReviewAction | None, str | None]:
    """T114：严格解析恢复值。

    返回 (action, invalid_reason)：
    - 合法（ReviewAction 枚举 / 合法枚举值字符串 / dict 携带合法 review_action）
      → (action, None)；
    - 非法（未知字符串、非法枚举、dict 缺失或携带非法值、不支持类型）与缺失
      （None / 空串 / 空 dict 值）→ (None, 原因描述)，由调用方保守落待复核。
    """
    if isinstance(review_value, ReviewAction):
        return review_value, None
    if isinstance(review_value, str):
        if not review_value:
            return None, "empty review_action string"
        try:
            return ReviewAction(review_value), None
        except ValueError:
            return None, f"unknown review_action string: {review_value!r}"
    if isinstance(review_value, dict):
        ra = review_value.get("review_action")
        if isinstance(ra, ReviewAction):
            return ra, None
        if isinstance(ra, str) and ra:
            try:
                return ReviewAction(ra), None
            except ValueError:
                return None, f"unknown review_action in dict: {ra!r}"
        return None, f"missing or invalid review_action in dict: {ra!r}"
    if review_value is None:
        return None, "missing review payload (None)"
    return None, f"unsupported review payload type: {type(review_value).__name__}"


async def human_review_node(state: ComplaintSessionState) -> dict:
    """人工复核断点：interrupt 等待人工输入，根据 review_action 更新状态。"""
    review_value = interrupt(
        {"prompt": "请人工复核判责结果与产出物", "session_id": state.session_id}
    )

    # T114：恢复值先经严格校验；非法/缺失保守落待复核，禁止默认 approve
    review_action, invalid_reason = _parse_review_action(review_value)
    if review_action is None:
        message = invalid_reason or "missing review_action"
        logger.warning(
            "human_review_invalid_recovery_value",
            detail=message,
            session_id=state.session_id,
        )
        return {
            "human_review": {
                "review_action": None,
                "reject_count": state.reject_count,
            },
            "exception_info": ExceptionInfo(
                error_type=_ERROR_TYPE_INVALID_REVIEW_ACTION,
                message=message[:300],
                node="human_review",
            ).model_dump(),
            "task_status": TaskStatus.pending_review.value,
        }

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
