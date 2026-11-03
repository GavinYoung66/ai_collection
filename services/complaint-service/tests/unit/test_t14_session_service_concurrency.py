"""T14：SessionService 半锁收敛回归单测。

整改口径：确认「全部内存读写都在同一事件循环线程」后移除 ``threading.Lock``，
并在类 docstring 固化单线程前提；本文件同时验证该前提的可观测形态与状态机行为。
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import time

import pytest

from app.models.state import TaskStatus
from app.service.session_service import SessionService


def _module_source() -> str:
    return inspect.getsource(sys.modules[SessionService.__module__])


def test_no_partial_lock_remains():
    """半锁必须消失：实例不再持有锁，模块不再依赖 threading。"""
    service = SessionService()
    assert not hasattr(service, "_lock")
    assert "import threading" not in _module_source()
    assert "threading.Lock" not in _module_source()


def test_class_documents_single_thread_premise():
    """单线程前提以注释/docstring 固化，避免后人误判为线程安全。"""
    doc = inspect.getdoc(SessionService) or ""
    assert "事件循环线程" in doc
    assert "线程池" in doc


def test_idempotent_create_returns_same_session():
    service = SessionService()
    sid = service.create_session("c-t14-1", "投诉正文")
    assert service.create_session("c-t14-1", "投诉正文") == sid
    assert len(service._store) == 1


def test_create_with_explicit_session_id_is_honored():
    service = SessionService()
    assert service.create_session("c-t14-2", "x", session_id="sid-fixed") == "sid-fixed"
    assert service.get_status("sid-fixed") == TaskStatus.pending.value


def test_valid_single_step_and_pipeline_multi_step():
    service = SessionService()
    sid = service.create_session("c-t14-3", "x")
    assert service.transition(sid, TaskStatus.evidence_gathering) is True
    assert service.transition(sid, TaskStatus.judging) is True
    # 流水线内的多步推进允许
    assert service.transition(sid, TaskStatus.pending_review) is True
    assert service.get_status(sid) == TaskStatus.pending_review.value


def test_illegal_transition_and_missing_session_are_rejected():
    service = SessionService()
    sid = service.create_session("c-t14-4", "x")
    assert service.transition(sid, TaskStatus.closed) is False
    assert service.transition("sid-missing", TaskStatus.judging) is False
    assert service.get_status("sid-missing") is None


def test_unknown_status_value_raises():
    service = SessionService()
    sid = service.create_session("c-t14-5", "x")
    with pytest.raises(ValueError):
        service.transition(sid, "not-a-status")


def test_timeout_window_is_strict_greater_than():
    """超时判定为严格大于：恰好 30 分钟不转人工，多 1 秒才转。"""
    base = time.time()
    service = SessionService(clock=lambda: base + 30 * 60)
    sid = service.create_session("c-t14-6", "x")
    service.transition(sid, TaskStatus.evidence_gathering)
    service.transition(sid, TaskStatus.judging)
    assert service.timeout_sessions() == []
    service._clock = lambda: base + 30 * 60 + 1
    assert service.timeout_sessions() == [sid]
    assert service.get_status(sid) == TaskStatus.pending_review.value


@pytest.mark.asyncio
async def test_concurrent_async_create_yields_one_session():
    """事件循环内并发进入 create_session 只会登记一个 session。

    create_session 全程无 await，检查与登记在单线程内不可分割。
    """

    class Redis:
        def __init__(self) -> None:
            self._d: dict[str, str] = {}

        def get(self, k: str):
            return self._d.get(k)

        def set(self, k: str, v: str, ex=None):
            self._d[k] = v
            return True

        def setnx(self, k: str, v: str) -> bool:
            if k in self._d:
                return False
            self._d[k] = v
            return True

        def expire(self, k: str, ttl: int) -> bool:
            return k in self._d

        def delete(self, k: str) -> int:
            return 1 if self._d.pop(k, None) is not None else 0

    service = SessionService(redis_client=Redis())

    async def create() -> str:
        return service.create_session("c-t14-7", "并发建单")

    sids = await asyncio.gather(*(create() for _ in range(8)))
    assert len(set(sids)) == 1
    assert len(service._store) == 1
    assert service._by_complaint["c-t14-7"] == sids[0]
