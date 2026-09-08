"""LangGraph 条件边单测（三条边全分支）。"""
from __future__ import annotations

import pytest

from app.graph.edges import (
    should_proceed_after_review,
    should_proceed_after_verdict,
    should_retry_or_proceed,
)
from app.models.state import (
    ComplaintSessionState,
    ExceptionInfo,
    HumanReviewResult,
    ReflectionReport,
    ReviewAction,
    TaskStatus,
    VerdictDraft,
    VerdictType,
)


def _state(**kw) -> ComplaintSessionState:
    base = dict(session_id="s1", trace_id="t1", complaint_id="cmp1", complaint_text="x")
    base.update(kw)
    return ComplaintSessionState(**base)


class TestShouldRetryOrProceed:
    def test_exception_to_manual(self):
        s = _state(exception_info=ExceptionInfo(error_type="mcp", message="e"))
        assert should_retry_or_proceed(s) == "to_manual"

    def test_insufficient_within_limit_to_re_gather(self):
        s = _state(
            loop_count=0,
            max_loops=3,
            reflection_report=ReflectionReport(evidence_sufficient=False),
        )
        assert should_retry_or_proceed(s) == "to_re_gather"

    def test_insufficient_exceeded_to_manual(self):
        s = _state(
            loop_count=3,
            max_loops=3,
            reflection_report=ReflectionReport(evidence_sufficient=False),
        )
        assert should_retry_or_proceed(s) == "to_manual"

    def test_sufficient_to_judgment(self):
        s = _state(reflection_report=ReflectionReport(evidence_sufficient=True))
        assert should_retry_or_proceed(s) == "to_judgment"


class TestShouldProceedAfterVerdict:
    def test_pending_review_to_review(self):
        s = _state(
            task_status=TaskStatus.pending_review,
            exception_info=ExceptionInfo(error_type="llm_degraded", message="none"),
        )
        assert should_proceed_after_verdict(s) == "to_review"

    def test_missing_verdict_draft_to_review(self):
        s = _state()
        assert s.verdict_draft is None
        assert should_proceed_after_verdict(s) == "to_review"

    def test_has_verdict_to_package(self):
        s = _state(verdict_draft=VerdictDraft(verdict=VerdictType.pending, confidence=0.9))
        assert should_proceed_after_verdict(s) == "to_package"


class TestShouldProceedAfterReview:
    def test_reject_within_limit_to_re_gather(self):
        s = _state(
            reject_count=1,
            human_review=HumanReviewResult(review_action=ReviewAction.reject_rejudge),
        )
        assert should_proceed_after_review(s) == "to_re_gather"

    def test_reject_exceeded_to_manual(self):
        s = _state(
            reject_count=3,
            human_review=HumanReviewResult(review_action=ReviewAction.reject_rejudge),
        )
        assert should_proceed_after_review(s) == "to_manual"

    def test_escalate_to_senior_review(self):
        s = _state(human_review=HumanReviewResult(review_action=ReviewAction.escalate))
        assert should_proceed_after_review(s) == "to_senior_review"

    def test_approve_to_backflow(self):
        s = _state(human_review=HumanReviewResult(review_action=ReviewAction.approve))
        assert should_proceed_after_review(s) == "to_backflow"
