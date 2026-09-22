"""common.resilience.queue 单测：未满入队/满拒绝不丢/超时 pop 返回 None/join 语义。"""
import asyncio
import time

import pytest

from common.resilience.queue import BoundedQueue


def test_invalid_max_len():
    with pytest.raises(ValueError):
        BoundedQueue(0)


@pytest.mark.asyncio
async def test_push_under_capacity():
    q = BoundedQueue(max_len=100)
    assert await q.push("a") is True
    assert q.qsize() == 1
    assert not q.full()


@pytest.mark.asyncio
async def test_push_at_capacity_returns_false_without_drop():
    q = BoundedQueue(max_len=3)
    for item in ("a", "b", "c"):
        assert await q.push(item) is True
    assert q.full()

    rejected = {"id": "should-not-enter"}
    assert await q.push(rejected) is False
    # 拒绝后队列长度不变，被拒数据没有进入队列（不静默丢数据）
    assert q.qsize() == 3
    drained = [await q.pop(0.01) for _ in range(3)]
    assert drained == ["a", "b", "c"]
    assert rejected not in drained


@pytest.mark.asyncio
async def test_pop_returns_item_fifo():
    q = BoundedQueue(max_len=10)
    await q.push(1)
    await q.push(2)

    assert await q.pop(timeout=0.01) == 1
    assert await q.pop(timeout=0.01) == 2


@pytest.mark.asyncio
async def test_pop_empty_queue_times_out_returns_none():
    q = BoundedQueue(max_len=10)
    start = time.monotonic()

    assert await q.pop(timeout=0.05) is None

    elapsed = time.monotonic() - start
    assert 0.05 <= elapsed < 0.5


@pytest.mark.asyncio
async def test_slot_reusable_after_pop():
    q = BoundedQueue(max_len=1)
    assert await q.push("x") is True
    assert await q.push("y") is False
    assert await q.pop(timeout=0.01) == "x"
    assert await q.push("y") is True


@pytest.mark.asyncio
async def test_pop_marks_task_done_for_join():
    q = BoundedQueue(max_len=10)
    await q.push("a")
    assert await q.pop(timeout=0.01) == "a"

    # pop 取出即 task_done：join() 立即返回，优雅关停语义可用
    await asyncio.wait_for(q._queue.join(), timeout=0.1)


@pytest.mark.asyncio
async def test_pop_nowait_nonblocking():
    """pop_nowait：空队列立即 None（不等待）；有数据 FIFO 取出且计 task_done。"""
    q = BoundedQueue(max_len=10)
    assert q.pop_nowait() is None
    await q.push("a")
    await q.push("b")
    assert q.pop_nowait() == "a"
    assert q.pop_nowait() == "b"
    assert q.pop_nowait() is None
    await asyncio.wait_for(q._queue.join(), timeout=0.1)
