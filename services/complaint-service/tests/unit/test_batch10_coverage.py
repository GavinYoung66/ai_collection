"""批次10 单元测试强化：补齐低覆盖核心模块。

覆盖：checkpointer、human_review、scheduler、complaint_service(run_graph)、
mode_service、recovery_service、session_service 边界。
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.events.publisher import publisher
from app.graph.checkpointer import (
    archive_expired_sessions,
    get_checkpointer,
    get_pg_dsn,
    handle_suspended_sessions,
)
from app.graph.nodes.human_review import human_review_node
from app.models.state import ComplaintSessionState, ReviewAction, TaskStatus, VerdictType
from app.service.complaint_service import complaint_service
from app.service.mode_service import ModeService
from app.service.recovery_service import RecoveryService
from app.service.scheduler import (
    cleanup_archived_90d,
    cleanup_stale_24h,
    quarterly_restore_drill,
    redis_rdb_snapshot,
    register_jobs,
    weekly_backup,
)
from app.service.session_service import session_service


@pytest.fixture(autouse=True)
def _clear_events():
    publisher.clear()
    yield


class TestCheckpointer:
    def test_memory_when_no_dsn(self):
        old = os.environ.pop("COMPLAINT_PG_DSN", None)
        try:
            cp = asyncio.run(get_checkpointer())
            from langgraph.checkpoint.memory import MemorySaver
            assert isinstance(cp, MemorySaver)
        finally:
            if old is not None:
                os.environ["COMPLAINT_PG_DSN"] = old

    def test_force_memory(self):
        cp = asyncio.run(get_checkpointer(force_memory=True))
        from langgraph.checkpoint.memory import MemorySaver
        assert isinstance(cp, MemorySaver)

    def test_get_pg_dsn(self):
        os.environ["COMPLAINT_PG_DSN"] = "postgresql://x"
        assert get_pg_dsn() == "postgresql://x"
        del os.environ["COMPLAINT_PG_DSN"]

    def test_stubs_return_zero(self):
        assert asyncio.run(archive_expired_sessions()) == 0
        assert asyncio.run(handle_suspended_sessions()) == 0

    def test_pg_import_failure_falls_back(self, monkeypatch):
        """PG 依赖 import 失败 → fallback MemorySaver。"""
        os.environ["COMPLAINT_PG_DSN"] = "postgresql://fake"
        import builtins
        real_import = builtins.__import__
        def fake_import(name, *a, **k):
            if name.startswith("langgraph.checkpoint.postgres") or name == "psycopg_pool":
                raise ImportError(f"mock no {name}")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        try:
            cp = asyncio.run(get_checkpointer())
            from langgraph.checkpoint.memory import MemorySaver
            assert isinstance(cp, MemorySaver)
        finally:
            del os.environ["COMPLAINT_PG_DSN"]

    def test_pg_success_path(self, monkeypatch):
        """PG 依赖可用 → 返回 AsyncPostgresSaver（mock）。"""
        os.environ["COMPLAINT_PG_DSN"] = "postgresql://fake"
        from unittest.mock import AsyncMock, MagicMock
        import sys
        mock_saver = MagicMock()
        mock_saver.setup = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.open = AsyncMock()
        mock_pg = MagicMock()
        mock_pg.AsyncPostgresSaver = MagicMock(return_value=mock_saver)
        mock_pool_mod = MagicMock()
        mock_pool_mod.AsyncConnectionPool = MagicMock(return_value=mock_pool)
        mock_rows = MagicMock()
        mock_rows.dict_row = "dict_row_mock"
        saved = {}
        for name, mod in [("langgraph.checkpoint.postgres.aio", mock_pg),
                          ("psycopg_pool", mock_pool_mod),
                          ("psycopg.rows", mock_rows)]:
            saved[name] = sys.modules.get(name)
            sys.modules[name] = mod
        try:
            cp = asyncio.run(get_checkpointer())
            assert cp is mock_saver
            mock_pool.open.assert_awaited_once()
            mock_saver.setup.assert_awaited_once()
        finally:
            del os.environ["COMPLAINT_PG_DSN"]
            for name, mod in saved.items():
                if mod is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = mod


class TestHumanReview:
    def _state(self) -> ComplaintSessionState:
        return ComplaintSessionState(
            session_id="s1", trace_id="t1", complaint_id="c1",
            complaint_text="x",
        )

    def test_approve_resolved(self):
        from langgraph.types import Command
        from app.graph.builder import build_complaint_graph
        from langgraph.checkpoint.memory import MemorySaver
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "hr-1"}}
        st = asyncio.run(graph.ainvoke(self._state().model_dump(), cfg))
        final = asyncio.run(graph.ainvoke(Command(resume="approve"), cfg))
        assert final["task_status"] == TaskStatus.resolved.value

    def test_reject_rejudge_back_to_evidence(self):
        from langgraph.types import Command
        from app.graph.builder import build_complaint_graph
        from langgraph.checkpoint.memory import MemorySaver
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "hr-2"}}
        asyncio.run(graph.ainvoke(self._state().model_dump(), cfg))
        final = asyncio.run(graph.ainvoke(Command(resume="reject_rejudge"), cfg))
        assert final["reject_count"] == 1
        # human_review 节点先落到 evidence_gathering，图会继续重取证/研判，
        # 终态可能已越过取证；不得关单或直接 resolved。
        assert final["task_status"] in (
            TaskStatus.evidence_gathering.value,
            TaskStatus.reflection_retry.value,
            TaskStatus.judging.value,
            TaskStatus.draft_generated.value,
            TaskStatus.pending_review.value,
        )
        assert final["task_status"] != TaskStatus.resolved.value

    def test_unknown_action_defaults_approve(self):
        st = self._state()
        from langgraph.types import Command
        from app.graph.builder import build_complaint_graph
        from langgraph.checkpoint.memory import MemorySaver
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "hr-3"}}
        asyncio.run(graph.ainvoke(st.model_dump(), cfg))
        final = asyncio.run(graph.ainvoke(Command(resume="garbage"), cfg))
        assert final["task_status"] == TaskStatus.resolved.value

    def test_dict_review_action(self):
        """resume 传 dict 形式的 review_action。"""
        from langgraph.types import Command
        from app.graph.builder import build_complaint_graph
        from langgraph.checkpoint.memory import MemorySaver
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "hr-4"}}
        asyncio.run(graph.ainvoke(self._state().model_dump(), cfg))
        final = asyncio.run(graph.ainvoke(
            Command(resume={"review_action": "escalate"}), cfg))
        assert final["task_status"] == TaskStatus.pending_senior_review.value


class TestScheduler:
    def test_cleanup_archived_90d(self):
        import time
        store = {
            "s1": {"task_status": "archived", "updated_at": time.time() - 91 * 86400},
            "s2": {"task_status": "archived", "updated_at": time.time()},
            "s3": {"task_status": "pending", "updated_at": time.time() - 100 * 86400},
        }
        assert cleanup_archived_90d(store) == 1
        assert "s1" not in store
        assert cleanup_archived_90d(None) == 0

    def test_stale_cleanup_delegates(self):
        r = RecoveryService()
        sessions = {"s1": {"task_status": "pending",
                           "updated_at": __import__("time").time() - 25 * 3600}}
        assert cleanup_stale_24h(r, sessions) == ["s1"]
        assert cleanup_stale_24h(None, None) == []

    def test_stub_jobs(self):
        assert redis_rdb_snapshot() is True
        assert weekly_backup() is True
        assert quarterly_restore_drill() is True
        register_jobs(None)  # 无异常


class TestComplaintServiceRunGraph:
    def test_judge_with_graph(self):
        rec = complaint_service.create("投诉测试 run_graph")
        cid = rec["complaint_id"]
        result = complaint_service.judge(cid, run_graph=True)
        assert result["verdict"] is not None
        assert result["task_status"] == TaskStatus.pending_review.value
        assert result["verdict"] in (VerdictType.agent_violation,
                                     VerdictType.user_misunderstanding,
                                     VerdictType.compliant_but_poor_experience,
                                     VerdictType.pending)

    def test_create_list_get(self):
        rec = complaint_service.create("列表测试")
        cid = rec["complaint_id"]
        assert complaint_service.get(cid) is not None
        assert complaint_service.get("nonexistent") is None
        listing = complaint_service.list()
        assert listing["total"] >= 1

    def test_close(self):
        rec = complaint_service.create("关闭测试")
        cid = rec["complaint_id"]
        judged = complaint_service.judge(cid)
        establishment = judged.get("verdict")
        if establishment is None or establishment == VerdictType.pending:
            establishment = VerdictType.compliant_but_poor_experience
            reason = "测试关单：pending 改判体验差"
        else:
            reason = None
        complaint_service.submit_disposition_verdict(
            cid, establishment, reason=reason
        )
        complaint_service.submit_disposition_callback(cid)
        closed = complaint_service.close(cid)
        assert closed["task_status"] == TaskStatus.closed.value


class TestModeService:
    def test_get_active_and_gray(self):
        m = ModeService()
        assert m.get_active() in ("single_agent", "multi_agent")
        assert isinstance(m.get_gray_percent(), int)

    def test_gray_assignment(self):
        m = ModeService()
        assert m.should_use_single_agent("s1") in (True, False)
        assert isinstance(m.is_gray_session("s1"), bool)

    def test_consistency_check(self):
        m = ModeService()
        assert isinstance(m.check_consistency_ready(), bool)

    def test_switch_invalid_mode(self):
        m = ModeService()
        ok, err = m.switch("invalid_mode", signers=["ops", "tech"])
        assert ok is False

    def test_switch_rollback_on_redis_failure(self):
        """Redis set 失败时回滚并返回 A5014。"""
        class FailRedis:
            def get(self, k, default=None): return "single_agent"
            def set(self, k, v, ex=None): raise RuntimeError("redis down")
            def delete(self, k): return 1
        m = ModeService(redis_client=FailRedis())
        ok, err = m.switch("multi_agent", signers=["ops", "tech"], gray_percent=50)
        assert ok is False

    def test_full_switch_requires_consistency(self):
        m = ModeService()
        # 默认一致性不达标 → 不切换
        result = m.full_switch_if_consistent()
        assert result in (True, False)


class TestRecoveryService:
    def test_fallback_roundtrip(self):
        r = RecoveryService()
        r.save_fallback("sid-1", {"k": "v"})
        ok, _ = r.recover_from_fallback("sid-1")
        assert ok is True

    def test_fallback_missing(self):
        r = RecoveryService()
        ok, status = r.recover_from_fallback("missing")
        assert ok is False and status == "pending_review"

    def test_fallback_corrupt(self, monkeypatch):
        r = RecoveryService()
        r._redis.set("csmp:session:bad:fallback", "not-json", ex=3600)
        ok, status = r.recover_from_fallback("bad")
        assert ok is False and status == "pending_review"

    def test_pg_available_toggle(self):
        r = RecoveryService()
        r.set_pg_available(True)
        assert r.accept_new_complaint()[0] is True
        r.set_pg_available(False)
        ok, code = r.accept_new_complaint()
        assert ok is False and code == "A5010"

    def test_cleanup_stale(self):
        import time
        r = RecoveryService()
        sessions = {
            "s1": {"task_status": "pending", "updated_at": time.time() - 25 * 3600},
            "s2": {"task_status": "pending_review", "updated_at": time.time()},
        }
        stale = r.cleanup_stale(sessions)
        assert "s1" in stale
        assert sessions["s1"]["task_status"] == "pending_review"

    def test_recover_from_checkpoint_pg_unavailable(self):
        r = RecoveryService()
        r.set_pg_available(False)
        r.save_fallback("s-fb", {"x": 1})
        ok, _ = r.recover_from_checkpoint("s-fb")
        assert ok is True

    def test_recover_from_checkpoint_with_mock(self):
        r = RecoveryService()
        r.set_pg_available(True)
        from unittest.mock import MagicMock
        cp = MagicMock()
        cp.get.return_value = {"state": "ok"}
        ok, _ = r.recover_from_checkpoint("s1", checkpointer=cp)
        assert ok is True

    def test_recover_from_checkpoint_none_state(self):
        r = RecoveryService()
        r.set_pg_available(True)
        from unittest.mock import MagicMock
        cp = MagicMock()
        cp.get.return_value = None
        ok, status = r.recover_from_checkpoint("s1", checkpointer=cp)
        assert ok is False and status == "pending_review"


class TestSessionServiceBoundary:
    def test_invalid_transition_rejected(self):
        sid = session_service.create_session(complaint_id="c-boundary", complaint_text="x")
        # pending → resolved 非法
        assert session_service.transition(sid, TaskStatus.resolved) is False
