"""T06 回归：`_graph_inflight` 检查与登记必须原子，并发只允许一张图进入。"""
from __future__ import annotations

import asyncio

import pytest

from app.models.state import TaskStatus
from app.service import complaint_service as cs
from app.service.complaint_service import complaint_service


def _make_case(cid: str) -> None:
    complaint_service.create("并发在途守卫", case_id=cid)


@pytest.mark.asyncio
async def test_concurrent_judge_only_one_enters_graph(monkeypatch):
    """两个并发请求：仅一个进入 `_judge_with_graph`，另一个返回当前态。"""
    cid = "t06-concurrent-001"
    _make_case(cid)

    started: list[str] = []
    release = asyncio.Event()

    async def _slow_judge(rec, complaint_id):
        started.append(complaint_id)
        await release.wait()
        return rec

    monkeypatch.setattr(complaint_service, "_judge_with_graph", _slow_judge)

    tasks = [
        asyncio.create_task(complaint_service.judge_async(cid, run_graph=True))
        for _ in range(2)
    ]
    # 让两个请求都跑过入口，第一个已登记在途、第二个应命中守卫
    await asyncio.sleep(0.05)
    assert started == [cid], "并发请求不得双跑图"

    release.set()
    outs = await asyncio.gather(*tasks)

    assert all(o["complaint_id"] == cid for o in outs)
    assert cs.is_graph_inflight(cid) is False


@pytest.mark.asyncio
async def test_redis_unavailable_rejects_all_entries(monkeypatch):
    """Q-001 裁决（fail-closed）：Redis 不可用时所有进入都被保守拒绝，
    不跑图、工单转 pending_review、在途登记释放，绝不退化双跑。"""
    from app.service import graph_lock as gl

    cid = "t06-redis-down-001"
    _make_case(cid)

    async def _unavailable(complaint_id, ttl, token=None):  # noqa: ARG001
        return gl.LOCK_UNAVAILABLE

    started: list[str] = []
    release = asyncio.Event()

    async def _slow_judge(rec, complaint_id):
        started.append(complaint_id)
        await release.wait()
        return rec

    monkeypatch.setattr(cs, "acquire_graph_lock", _unavailable)
    monkeypatch.setattr(complaint_service, "_judge_with_graph", _slow_judge)

    tasks = [
        asyncio.create_task(complaint_service.judge_async(cid, run_graph=True))
        for _ in range(2)
    ]
    await asyncio.sleep(0.05)
    assert started == [], "Redis 不可用时任何请求都不得进入图执行（fail-closed）"

    release.set()
    outs = await asyncio.gather(*tasks)
    assert all(
        o["task_status"] == TaskStatus.pending_review.value for o in outs
    ), "被拒绝的请求应转 pending_review 人工兜底"
    assert cs.is_graph_inflight(cid) is False


@pytest.mark.asyncio
async def test_inflight_released_when_graph_raises(monkeypatch):
    """图执行抛异常后登记必须释放，后续请求还能再次进入。"""
    cid = "t06-raise-001"
    _make_case(cid)

    calls: list[str] = []

    async def _boom(rec, complaint_id):
        calls.append(complaint_id)
        raise RuntimeError("graph exploded")

    monkeypatch.setattr(complaint_service, "_judge_with_graph", _boom)

    with pytest.raises(RuntimeError):
        await complaint_service.judge_async(cid, run_graph=True)

    assert cs.is_graph_inflight(cid) is False
    assert calls == [cid]


@pytest.mark.asyncio
async def test_inflight_released_when_lock_held(monkeypatch):
    """跨进程锁被他人持有时跳过，但进程内登记不得残留。"""
    from app.service import graph_lock as gl

    cid = "t06-lock-held-001"
    _make_case(cid)

    async def _held(complaint_id, ttl, token=None):  # noqa: ARG001
        return gl.LOCK_HELD

    monkeypatch.setattr(cs, "acquire_graph_lock", _held)

    out = await complaint_service.judge_async(cid, run_graph=True)

    assert out["task_status"] == TaskStatus.pending.value
    assert cs.is_graph_inflight(cid) is False


@pytest.mark.asyncio
async def test_sequential_calls_after_completion(monkeypatch):
    """前一次执行结束后登记释放，后续串行调用可正常进入。"""
    cid = "t06-sequential-001"
    _make_case(cid)

    calls: list[str] = []

    async def _fast(rec, complaint_id):
        calls.append(complaint_id)
        return rec

    monkeypatch.setattr(complaint_service, "_judge_with_graph", _fast)

    await complaint_service.judge_async(cid, run_graph=True)
    await complaint_service.judge_async(cid, run_graph=True)

    assert calls == [cid, cid]
    assert cs.is_graph_inflight(cid) is False
