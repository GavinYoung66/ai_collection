"""批次9 灰度降级灾备 + 事件联动 单测。

覆盖：11 降级场景、5 维限流、4 频道 Pub-Sub、停止致电、灾备恢复、A5014。
依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import os
import time

import pytest

from app.events.publisher import publisher
from app.models.state import ReviewAction, VerdictType
from app.service.complaint_service import complaint_service
from app.service.degrade_flow import DegradeFlow
from app.service.degrade_llm import DegradeLLM
from app.service.degrade_mcp import DegradeMCP
from app.service.mode_service import ModeService
from app.service.rate_limiter import RateLimiter
from app.service.recovery_service import RecoveryService
from app.service.review_service import RejectReason, ReviewService
from app.service.scheduler import cleanup_stale_24h, redis_rdb_snapshot
from common.errors.codes import ErrorCode


@pytest.fixture(autouse=True)
def _clear_events():
    publisher.clear()
    yield


# ── 9.1 mode ──

class TestMode:
    def test_single_signature_rejected(self):
        ok, err = ModeService().switch("multi_agent", signers=["ops"])
        assert ok is False and err == ErrorCode.A5014

    def test_dual_signature_success(self):
        ok, err = ModeService().switch("multi_agent", signers=["ops", "tech"], gray_percent=50)
        assert ok and err is None

    def test_consistency_full_switch(self):
        class C:
            def get_ai_human_rate(self): return 0.9
            def get_cross_rate(self): return 0.85
        assert ModeService(consistency_provider=C()).full_switch_if_consistent() is True

    def test_mode_switched_event(self):
        ModeService().switch("multi_agent", signers=["ops", "tech"], gray_percent=100)
        evs = publisher.list_events("complaint.mode.switched")
        assert evs and "trace_id" in evs[0].payload

    def test_fail_redis_rollback_a5014(self):
        class FailRedis:
            def get(self, k):
                if "gray" in k:
                    return "0"
                return "single_agent"

            def set(self, *a, **k):
                raise RuntimeError("down")

        ok, err = ModeService(redis_client=FailRedis()).switch(
            "multi_agent", signers=["ops", "tech"],
        )
        assert ok is False and err == ErrorCode.A5014

    def test_dirty_gray_still_a5014_on_set_fail(self):
        """脏 gray 值不得抛 ValueError，set 失败仍回 A5014。"""
        class DirtyFail:
            def get(self, k):
                return "single_agent"  # 对 gray key 也脏

            def set(self, *a, **k):
                raise RuntimeError("down")

        ok, err = ModeService(redis_client=DirtyFail()).switch(
            "multi_agent", signers=["ops", "tech"],
        )
        assert ok is False and err == ErrorCode.A5014


# ── 9.5 rate_limiter ──

class TestRateLimiter:
    def test_complaint_idempotent_lock(self):
        r = RateLimiter()
        assert r.acquire_complaint("c1") is True
        assert r.acquire_complaint("c1") is False

    def test_mcp_llm_token_bucket(self):
        r = RateLimiter()
        assert r.acquire_mcp("rag") is True
        assert r.acquire_llm("qwen_turbo") is True

    def test_config_from_yaml(self):
        assert RateLimiter().config["complaint"]["max_active"] == 20

    def test_consume_pending_dim4(self):
        r = RateLimiter(config={
            "complaint": {"max_active": 1, "queue_max": 500,
                          "queue_timeout_minutes": 10, "lock_ttl_seconds": 1800},
            "mcp": {"qps": {"rag": 20}},
            "llm": {"qps": {"qwen_turbo": 30}},
            "queue": {"dynamic_consume": True, "base_consume_rate": 5},
        })
        assert r.acquire_complaint("active") is True
        assert r.acquire_complaint("queued") is False  # 入队
        r.release_complaint()
        consumed = r.consume_pending(max_n=5)
        assert "queued" in consumed


# ── 9.2a degrade_llm ──

class TestDegradeLLM:
    def test_three_scenarios_codes(self):
        d = DegradeLLM()
        st = {}
        d.handle_judge_failure(st)
        d.handle_output_failure(st)
        d.handle_rag_failure(st)
        codes = {e["code"] for e in st["exception_info"]}
        assert codes == {"A5004", "A5010", "A5005"}


# ── 9.2b degrade_mcp ──

class TestDegradeMCP:
    def test_four_scenarios(self):
        m = DegradeMCP()
        st = {}
        m.handle_business_ticket_failure(st)
        m.handle_call_quality_failure(st)
        codes = {e["code"] for e in st["exception_info"]}
        assert "A5006" in codes and "A5008" in codes
        assert m.handle_writeback_failure(state=st) is False
        assert any(e["code"] == "A5013" for e in st["exception_info"])
        assert m.handle_permission_denied("mcp", "rag") is False


# ── 9.2c degrade_flow ──

class TestDegradeFlow:
    def test_loop_exceeded(self):
        f = DegradeFlow()
        st = {}
        assert f.handle_loop_exceeded(3, st) == "pending_review"
        assert st["task_status"] == "pending_review"

    def test_steps_token_timeout(self):
        f = DegradeFlow()
        assert f.handle_steps_exceeded(100, 50) == "pending_review"
        assert f.handle_token_exceeded(9999, 1000) == "pending_review"
        assert f.handle_global_timeout(40 * 60) == "pending_review"

    def test_degraded_event_published(self):
        DegradeFlow().handle_loop_exceeded(3, {})
        assert any(e.topic == "complaint.degraded" for e in publisher.list_events())


# ── 9.3 recovery ──

class TestRecovery:
    def test_fallback_recovery(self):
        r = RecoveryService()
        assert r.recover_from_fallback("s1")[0] is False
        r.save_fallback("s1", {"x": 1})
        ok, _ = r.recover_from_fallback("s1")
        assert ok is True

    def test_pg_unavailable_rejects(self):
        r = RecoveryService()
        r.set_pg_available(False)
        ok, code = r.accept_new_complaint()
        assert ok is False and code == "A5010"

    def test_stale_cleanup(self):
        r = RecoveryService()
        sessions = {"s1": {"task_status": "pending",
                           "updated_at": time.time() - 25 * 3600}}
        assert cleanup_stale_24h(recovery_service=r, session_store=sessions) == ["s1"]


# ── 9.4 scheduler ──

class TestScheduler:
    def test_jobs_callable(self):
        assert redis_rdb_snapshot() is True


# ── 11.1 EventPublisher 单真相 ──

class TestEventPublisher:
    def test_no_dual_service_publisher(self):
        assert not os.path.exists("app/service/event_publisher.py")

    def test_four_channels(self):
        for ch in ("complaint.judged", "complaint.closed",
                   "complaint.insight.weekly", "complaint.mode.switched"):
            publisher.publish(ch, {"data": 1})
            evs = publisher.list_events(ch)
            assert evs and "trace_id" in evs[0].payload


# ── 11.2 停止致电 ──

class TestStopCall:
    def test_agent_violation_stops_call(self):
        rec = complaint_service.create("投诉")
        cid = rec["complaint_id"]
        complaint_service.judge(cid)
        assert complaint_service.get_verdict(cid)["stop_call_flag"] is True
        assert any(e.topic == "case.risk.upgraded" for e in publisher.list_events())

    def test_non_violation_restores_call(self):
        rec = complaint_service.create("投诉2")
        cid = rec["complaint_id"]
        complaint_service.judge(cid)
        ReviewService().submit(
            cid, ReviewAction.modify_verdict, reason=RejectReason.other,
            new_verdict=VerdictType.user_misunderstanding,
        )
        assert complaint_service.get_verdict(cid)["stop_call_flag"] is False
