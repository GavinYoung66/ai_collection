from app.models.state import ComplaintSessionState


def should_retry_or_proceed(state: ComplaintSessionState) -> str:
    if state.exception_info.has_exception:
        return "to_manual"
    if state.reflection_report is None:
        return "to_reflection"
    if not state.reflection_report.evidence_sufficient:
        if state.loop_count < state.max_loops:
            return "to_re_gather"
        else:
            return "to_manual"
    return "to_judgment"


def should_proceed_after_review(state: ComplaintSessionState) -> str:
    action = state.human_review.review_action
    if action == "reject_rejudge":
        if state.human_review.reject_count <= 2:
            return "to_re_gather"
        else:
            return "to_manual"
    if action == "escalate":
        return "to_senior_review"
    return "to_backflow"
