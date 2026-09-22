"""common.resilience.concurrency 单测：有空位即取/满即 False/释放后可再取。"""
import asyncio

import pytest

from common.resilience.concurrency import Semaphore


def test_invalid_max_inflight():
    with pytest.raises(ValueError):
        Semaphore(0)


@pytest.mark.asyncio
async def test_acquire_while_slot_available():
    sem = Semaphore(max_inflight=5)
    assert sem.inflight == 0

    assert await sem.acquire() is True
    assert await sem.acquire() is True
    assert sem.inflight == 2


@pytest.mark.asyncio
async def test_full_returns_false_without_blocking():
    sem = Semaphore(max_inflight=3)
    for _ in range(3):
        assert await sem.acquire() is True

    # 容量满：立即 False（不等待）
    assert await sem.acquire() is False
    assert sem.inflight == 3


@pytest.mark.asyncio
async def test_release_frees_slot():
    sem = Semaphore(max_inflight=2)
    assert await sem.acquire() is True
    assert await sem.acquire() is True
    assert await sem.acquire() is False

    sem.release()
    assert await sem.acquire() is True
    assert await sem.acquire() is False


@pytest.mark.asyncio
async def test_concurrent_acquires_exactly_max_pass():
    sem = Semaphore(max_inflight=5)
    results = await asyncio.gather(*[sem.acquire() for _ in range(6)])

    assert sum(results) == 5       # 恰好 5 个通过
    assert results.count(False) == 1  # 第 6 个被拒
    assert sem.inflight == 5
    assert await sem.acquire() is False


@pytest.mark.asyncio
async def test_over_release_raises():
    sem = Semaphore(max_inflight=1)
    with pytest.raises(ValueError):
        sem.release()  # 未获取先释放
