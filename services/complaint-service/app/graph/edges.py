"""LangGraph 条件边路由。

三条条件边：
- should_retry_or_proceed：reflection 后路由（to_manual / to_re_gather / to_judgment）
- should_proceed_after_verdict：verdict 后路由（to_review / to_package）
- should_proceed_after_review：human_review 后路由（to_re_gather / to_manual / to_senior_review / to_backflow）
"""
from __future__ import annotations

from app.models.state import ComplaintSessionState, ReviewAction, TaskStatus


def should_retry_or_proceed(state: ComplaintSessionState) -> str:
    """reflection 后路由。"""
    # 异常 → 人工
    if state.exception_info is not None:
        return "to_manual"
    # 反思不足且未超限 → 重取证；超限后仍有证据则进入研判（避免空答辩）
    if state.reflection_report is not None and not state.reflection_report.evidence_sufficient:
        if state.loop_count < state.max_loops:
            return "to_re_gather"
        # max_loops 耗尽：有案件证据则强制研判，否则人工
        if state.case_evidence:
            return "to_judgment"
        return "to_manual"
    # 反思充分 → 研判扇出
    return "to_judgment"


def should_proceed_after_verdict(state: ComplaintSessionState) -> str:
    """verdict 后路由。"""
    if (
        state.task_status == TaskStatus.pending_review
        or (
            state.exception_info is not None
            and state.exception_info.error_type == "llm_degraded"
        )
        or state.verdict_draft is None
    ):
        return "to_review"
    return "to_package"


def should_proceed_after_review(state: ComplaintSessionState) -> str:
    """human_review 后路由。"""
    action = state.human_review.review_action if state.human_review else None

    if action == ReviewAction.reject_rejudge:
        if state.reject_count <= 2:
            return "to_re_gather"
        return "to_manual"

    if action == ReviewAction.escalate:
        return "to_senior_review"

    # approve / modify_verdict / modify_response / add_evidence
    return "to_backflow"
