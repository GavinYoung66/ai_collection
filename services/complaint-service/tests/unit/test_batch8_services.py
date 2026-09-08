"""批次8 业务服务全流程单测。

工单接收→session(pending)→judge stub→复核→回调/回流 全流程 Mock 跑通。
依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import pytest

from app.models.state import ReviewAction, TaskStatus, VerdictType
from app.service.backflow_service import BackflowService
from app.service.callback_service import CallbackService
from app.service.complaint_service import complaint_service
from app.service.metrics_service import MockClock, MetricsService
from app.service.reconcile import daily_reconcile
from app.service.review_service import RejectReason, ReviewService
from app.service.review_sla_service import ReviewRole, ReviewSLAService, SLALevel
from app.service.session_service import SessionService
from app.service.stats_service import StatsService


class TestSessionService:
    def test_idempotent_create(self):
        s = SessionService()
        sid = s.create_session("c1", "x")
        assert s.create_session("c1", "x") == sid

    def test_illegal_transition_blocked(self):
        s = SessionService()
        sid = s.create_session("c2", "x")
        assert s.transition(sid, TaskStatus.closed) is False

    def test_timeout_to_pending_review(self):
        s = SessionService(clock=lambda: __import__("time").time() + 40 * 60)
        sid = s.create_session("c3", "x")
        s.transition(sid, TaskStatus.evidence_gathering)
        s.transition(sid, TaskStatus.judging)
        assert sid in s.timeout_sessions()
        assert s.get_status(sid) == "pending_review"


class TestReviewService:
    def _seed(self):
        rec = complaint_service.create("投诉")
        complaint_service.judge(rec["complaint_id"])
        return rec["complaint_id"]

    def test_reject_count_cap_forces_escalate(self):
        cid = self._seed()
        r = ReviewService()
        for _ in range(3):
            r.submit(cid, ReviewAction.reject_rejudge, reason=RejectReason.wrong_verdict)
        assert complaint_service.get_status(cid)["task_status"] == TaskStatus.pending_senior_review

    def test_reason_required(self):
        cid = self._seed()
        with pytest.raises(ValueError):
            ReviewService().submit(cid, ReviewAction.modify_verdict)

    def test_verdict_no_normal(self):
        cid = self._seed()
        with pytest.raises(ValueError):
            ReviewService().submit(cid, ReviewAction.modify_verdict,
                                   reason=RejectReason.other, new_verdict="normal")

    def test_legal_verdict_accepted(self):
        cid = self._seed()
        ReviewService().submit(cid, ReviewAction.modify_verdict,
                               reason=RejectReason.other,
                               new_verdict=VerdictType.user_misunderstanding)


class TestReviewSLA:
    def test_readonly_cannot_submit(self):
        assert ReviewSLAService().can_submit(ReviewRole.readonly) is False

    def test_ai_human_diff(self):
        d = ReviewSLAService().compute_ai_human_diff("a", "a", "x", "y")
        assert d["verdict_match_rate"] == 1.0

    def test_sla_timeout(self):
        s = ReviewSLAService()
        s.set_sla("c", SLALevel.high_risk)
        assert s.check_timeout("c", 0, 3 * 3600) is True


class TestStatsService:
    def test_by_verdict_four_values(self):
        st = StatsService().get_stats()
        assert set(st["by_verdict"].keys()) == {
            "agent_violation", "user_misunderstanding",
            "compliant_but_poor_experience", "pending",
        }

    def test_consistency_alert(self):
        assert StatsService().check_consistency_alert() is True


class TestCallback:
    def test_dlq_on_3_failures(self):
        class Fail:
            def post(self, *a, **k): return False
        c = CallbackService(http_client=Fail())
        assert c.send("s", 1, {}) is False
        assert c.dlq_size() == 1

    def test_idempotent(self):
        c = CallbackService(http_client=None)
        assert c.send("s", 1, {}) is True
        assert c.send("s", 1, {}) is True  # 幂等


class TestBackflow:
    def test_zero_loss_on_failure(self):
        class Fail:
            def write(self, *a, **k): return False
        b = BackflowService(mcp_client=Fail())
        assert b.write_session("s", {}) is False
        assert b.retry_queue_size() >= 1


class TestMetrics:
    def test_alert_threshold(self):
        m = MetricsService(clock=MockClock())
        m.collector.record("mcp_failure_rate", 0.06)
        assert any(a["metric"] == "mcp_failure_rate" for a in m.collector.evaluate_alerts())

    def test_mock_clock_collect(self):
        m = MetricsService(clock=MockClock())
        assert m.maybe_collect() is None
        m.clock.advance(60)
        assert m.maybe_collect() is not None


class TestEndToEnd:
    """工单接收→session→judge→复核→回调/回流 全流程。"""

    def test_full_flow(self):
        # 1. 接收 → session pending
        rec = complaint_service.create("坐席态度差")
        cid = rec["complaint_id"]
        assert complaint_service.get_status(cid)["task_status"] == TaskStatus.pending

        # 2. judge stub → pending_review
        complaint_service.judge(cid)
        assert complaint_service.get_status(cid)["task_status"] == TaskStatus.pending_review
        v = complaint_service.get_verdict(cid)
        assert v["verdict"] in (
            VerdictType.agent_violation, VerdictType.user_misunderstanding,
            VerdictType.compliant_but_poor_experience, VerdictType.pending,
        )

        # 3. 人工复核 approve → resolved
        ReviewService().submit(cid, ReviewAction.approve)
        assert complaint_service.get_status(cid)["task_status"] == TaskStatus.resolved

        # 4. 回调 + 回流（Mock 成功）
        assert CallbackService().send(cid, 1, {"verdict": "resolved"}) is True
        assert BackflowService().write_session(cid, {"verdict": "resolved"}) is True

        # 5. close → closed + 事件
        complaint_service.close(cid, reason="完成")
        assert complaint_service.get_status(cid)["task_status"] == TaskStatus.closed

    def test_reconcile(self):
        s = SessionService()
        cb = CallbackService()
        sid = s.create_session("rx", "x")
        s.transition(sid, TaskStatus.evidence_gathering)
        s.transition(sid, TaskStatus.judging)
        s.transition(sid, TaskStatus.draft_generated)
        # 快进 2h
        s2 = SessionService(clock=lambda: __import__("time").time() + 2 * 3600)
        # 复用 sid 需重放：直接构造超时
        res = daily_reconcile(s, cb, stale_hours=0.0)
        assert "timed_out" in res or "stale_draft_generated" in res
