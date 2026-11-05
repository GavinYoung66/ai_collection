"""T47 / 1.2：图恢复任务 spawn_tracked 强引用 + 锁登记幂等回收 单测。

验收判据：
- 任务被 cancel 后 `_resume_lock_refs[cid]` 归零、登记 dict 无残留；
- 任务 done 但内部配对释放缺失时，done 回调兜底释放（防永久泄漏）；
- 多工单连续调度后 `_resume_locks`/`_resume_lock_refs` 不无界增长；
- 人审 approve 后 resume 调度可达（review() approve 分支触发）；
- 无事件循环（同步上下文）时 warning 跳过不抛。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from app.models.state import ReviewAction
from app.service import complaint_service as cs_mod
from app.service.complaint_service import complaint_service
from common.utils.spawn import _tracked_tasks


def _pending_tracked() -> list[asyncio.Task]:
    """当前运行 loop 的在途 tracked 任务（无 loop 的同步上下文返回空）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return []
    return [t for t in _tracked_tasks if t.get_loop() is loop and not t.done()]


async def _drain_done_callbacks() -> None:
    """让排程的 done_callback 执行完毕。"""
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _clean_resume_registry():
    """每个用例前后清空 resume 锁登记，防跨用例串扰。"""
    cs_mod._resume_locks.clear()
    cs_mod._resume_lock_refs.clear()
    cs_mod._resume_task_locks.clear()
    yield
    cs_mod._resume_locks.clear()
    cs_mod._resume_lock_refs.clear()
    cs_mod._resume_task_locks.clear()


class TestGraphResumeTracked:
    async def test_cancelled_resume_task_releases_lock_refs(self, monkeypatch):
        """任务被 cancel：内部 finally 释放 → refs 归零；done 回调 no-op 不双重释放。"""
        started = asyncio.Event()

        async def fake_resume(self, cid, sid):
            lock = cs_mod._resume_lock(cid)
            try:
                started.set()
                await asyncio.sleep(60)
            finally:
                cs_mod._release_resume_lock(cid, lock)

        monkeypatch.setattr(
            cs_mod.ComplaintService, "_resume_graph_thread", fake_resume
        )
        complaint_service._schedule_graph_resume("c-1", "s-1")

        await asyncio.wait_for(started.wait(), timeout=2)
        assert cs_mod._resume_lock_refs.get("c-1") == 1, "登记应在任务运行期持有"

        pending = _pending_tracked()
        assert pending, "图恢复任务应在 tracked 集合内（强引用持有）"
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*pending, return_exceptions=True)
        assert any(isinstance(r, asyncio.CancelledError) for r in results)
        await _drain_done_callbacks()

        assert "c-1" not in cs_mod._resume_lock_refs, "cancel 后引用计数应归零回收"
        assert "c-1" not in cs_mod._resume_locks, "cancel 后锁登记应回收"

    async def test_done_callback_releases_when_inner_cleanup_missing(
        self, monkeypatch
    ):
        """任务 done 但内部配对释放缺失（模拟异常中断清理）：done 回调兜底释放。"""

        async def fake_resume(self, cid, sid):
            cs_mod._resume_lock(cid)  # 登记后不释放（模拟 finally 缺失）
            # 直接 return：任务 done，登记残留

        monkeypatch.setattr(
            cs_mod.ComplaintService, "_resume_graph_thread", fake_resume
        )
        complaint_service._schedule_graph_resume("c-2", "s-2")
        await _drain_done_callbacks()

        assert "c-2" not in cs_mod._resume_lock_refs, (
            "done 回调应兜底释放引用计数（防永久泄漏）"
        )
        assert "c-2" not in cs_mod._resume_locks, "done 回调应兜底回收锁登记"

    async def test_defensive_release_skips_when_lock_rebound_by_later_task(self):
        """W3 核心规则：A 的旧句柄 (cid, lock_a) 与当前登记 lock_b 不同一，
        兜底释放 MUST 跳过——B 重建的引用计数与锁登记保持完好（旧实现按
        cid 盲查会误减 B 的计数）。"""
        lock_a, lock_b = asyncio.Lock(), asyncio.Lock()
        fake_task = asyncio.get_running_loop().create_task(asyncio.sleep(0))
        cs_mod._resume_task_locks[fake_task] = [("c-w3", lock_a)]
        cs_mod._resume_locks["c-w3"] = lock_b  # B 已重建登记
        cs_mod._resume_lock_refs["c-w3"] = 1  # B 的引用

        cs_mod._defensive_release_task_locks(fake_task)

        assert cs_mod._resume_lock_refs.get("c-w3") == 1, "B 的引用计数不得被误减"
        assert cs_mod._resume_locks.get("c-w3") is lock_b, "B 的锁登记不得被误收"
        assert fake_task not in cs_mod._resume_task_locks, "任务关联应被清除（幂等）"

    async def test_defensive_release_releases_own_still_registered_lock(self):
        """W3 泄漏兜底保留：A 中断未 finally，其句柄仍与当前登记同一把锁，
        兜底释放精确回收 A 自己的引用（不伤及后来者共享引用以外的计数）。"""
        lock_a = asyncio.Lock()
        fake_task = asyncio.get_running_loop().create_task(asyncio.sleep(0))
        cs_mod._resume_task_locks[fake_task] = [("c-w3", lock_a)]
        cs_mod._resume_locks["c-w3"] = lock_a
        cs_mod._resume_lock_refs["c-w3"] = 1  # A 残留引用

        cs_mod._defensive_release_task_locks(fake_task)

        assert "c-w3" not in cs_mod._resume_lock_refs, "A 残留引用应被兜底回收"
        assert "c-w3" not in cs_mod._resume_locks, "A 泄漏的锁登记应被回收"

    async def test_late_callback_race_behavioral(self, monkeypatch):
        """W3 行为级：A 泄漏 + B 重入共享同锁的竞态序列终态正确。"""
        # A：登记后不释放（模拟 finally 缺失中断）
        async def fake_resume_a(self, cid, sid):
            cs_mod._resume_lock(cid)

        monkeypatch.setattr(
            cs_mod.ComplaintService, "_resume_graph_thread", fake_resume_a
        )
        complaint_service._schedule_graph_resume("c-w3r", "s-w3r")
        await asyncio.sleep(0)  # A done（回调排程）
        await _drain_done_callbacks()  # 无后来者：A 残留应被兜底回收
        assert "c-w3r" not in cs_mod._resume_locks

        # B 重新调度并保持运行：新登记、新引用
        started_b = asyncio.Event()

        async def fake_resume_b(self, cid, sid):
            lock = cs_mod._resume_lock(cid)
            started_b.set()
            await asyncio.sleep(60)

        monkeypatch.setattr(
            cs_mod.ComplaintService, "_resume_graph_thread", fake_resume_b
        )
        complaint_service._schedule_graph_resume("c-w3r", "s-w3r")
        await asyncio.wait_for(started_b.wait(), timeout=2)
        await _drain_done_callbacks()  # A 的回调早已执行，不得影响 B

        assert cs_mod._resume_lock_refs.get("c-w3r") == 1, (
            "B 的引用计数应保持 1（A 的迟到回调不得误减）"
        )
        assert "c-w3r" in cs_mod._resume_locks, "B 运行期锁登记不得被误回收"

    async def test_no_unbounded_growth_across_complaints(self, monkeypatch):
        """多工单连续调度：正常完成路径下两个登记 dict 不无界增长。"""

        async def fake_resume(self, cid, sid):
            lock = cs_mod._resume_lock(cid)
            cs_mod._release_resume_lock(cid, lock)

        monkeypatch.setattr(
            cs_mod.ComplaintService, "_resume_graph_thread", fake_resume
        )
        for i in range(20):
            complaint_service._schedule_graph_resume(f"c-g{i}", "s-g")
        await _drain_done_callbacks()

        assert cs_mod._resume_locks == {}, "锁登记应全部回收（不无界增长）"
        assert cs_mod._resume_lock_refs == {}, "引用计数应全部归零回收"

    async def test_review_approve_triggers_resume_scheduling(self, monkeypatch):
        """人审 approve：review() 走 approve 分支调度图恢复（resume 流程可达）。"""
        from app.service.review_service import review_service

        rec = {"complaint_id": "c-5", "session_id": "s-5"}
        monkeypatch.setattr(review_service, "submit", Mock(return_value=rec))

        scheduled: list[tuple[str, str]] = []

        async def fake_resume(self, cid, sid):
            scheduled.append((cid, sid))

        monkeypatch.setattr(
            cs_mod.ComplaintService, "_resume_graph_thread", fake_resume
        )
        result = complaint_service.review("c-5", ReviewAction.approve)

        assert result == rec
        await _drain_done_callbacks()
        assert ("c-5", "s-5") in scheduled, "approve 后图恢复调度应可达"

    def test_sync_context_skips_with_warning(self, monkeypatch):
        """无事件循环（同步上下文）：warning 跳过不抛（不派发任务）。"""
        spy = Mock()
        monkeypatch.setattr(cs_mod.logger, "warning", spy)

        complaint_service._schedule_graph_resume("c-sync", "s-sync")

        spy.assert_called_once()
        assert spy.call_args.args[0] == "no_event_loop_for_graph_resume"
        assert spy.call_args.kwargs.get("complaint_id") == "c-sync"
        assert _pending_tracked() == [], "无 loop 时不应派发任何任务"