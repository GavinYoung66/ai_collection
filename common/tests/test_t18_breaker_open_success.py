"""T18：熔断器 OPEN 态成功语义单测。

规范：record_success 仅在 closed / half-open 态生效；open 态上报成功不得清零
失败计数、不得改变状态、不得重置/延长重置窗口。
"""
from __future__ import annotations

import pytest

from common.resilience.breaker import BreakerState, CircuitBreaker
from common.resilience.hooks import set_publisher


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_publisher():
    set_publisher(None)
    yield
    set_publisher(None)


async def _trip_open(cb: CircuitBreaker) -> None:
    for _ in range(cb.fail_max):
        await cb.record_failure(RuntimeError("boom"))
    assert cb.state == BreakerState.OPEN.value


@pytest.mark.asyncio
async def test_open_state_success_keeps_failures_and_state():
    clock = FakeClock()
    cb = CircuitBreaker("csmp:llm_degraded:m1", fail_max=3, reset_timeout=30, clock=clock)
    await _trip_open(cb)
    opened_at = cb._opened_at

    await cb.record_success()

    assert cb.state == BreakerState.OPEN.value
    assert cb.failures == cb.fail_max
    assert cb._opened_at == opened_at


@pytest.mark.asyncio
async def test_open_state_success_does_not_extend_reset_window():
    clock = FakeClock()
    cb = CircuitBreaker("csmp:llm_degraded:m1", fail_max=3, reset_timeout=30, clock=clock)
    await _trip_open(cb)

    clock.advance(10)
    await cb.record_success()  # 迟到成功不得顺延窗口

    clock.advance(19)  # 距跳闸 29s
    assert await cb.before_call() is False
    clock.advance(1)   # 距跳闸 30s：原窗口到期，放探针
    assert await cb.before_call() is True
    assert cb.state == BreakerState.HALF_OPEN.value


@pytest.mark.asyncio
async def test_open_state_success_emits_no_hook_event():
    events = []

    async def publisher(name, payload):
        events.append(name)

    set_publisher(publisher)
    clock = FakeClock()
    cb = CircuitBreaker("csmp:llm_degraded:m1", fail_max=2, reset_timeout=30, clock=clock)
    await _trip_open(cb)
    events.clear()

    await cb.record_success()
    await cb.record_success()
    assert events == []


@pytest.mark.asyncio
async def test_closed_state_success_still_resets_consecutive_failures():
    cb = CircuitBreaker("csmp:llm_degraded:m1", fail_max=3, clock=FakeClock())
    await cb.record_failure()
    await cb.record_failure()
    assert cb.failures == 2
    await cb.record_success()
    assert cb.failures == 0
    assert cb.state == BreakerState.CLOSED.value


@pytest.mark.asyncio
async def test_half_open_probe_success_closes_and_clears():
    clock = FakeClock()
    cb = CircuitBreaker("rag:llm_degraded:deepseek-chat", fail_max=2,
                        reset_timeout=30, clock=clock)
    await _trip_open(cb)
    clock.advance(30)
    assert await cb.before_call() is True
    assert cb.state == BreakerState.HALF_OPEN.value

    await cb.record_success()
    assert cb.state == BreakerState.CLOSED.value
    assert cb.failures == 0
    assert cb._opened_at is None
    assert await cb.before_call() is True


@pytest.mark.asyncio
async def test_open_success_then_failure_stays_open():
    """open 态连续的 success/failure 杂散上报都不改变状态机。"""
    clock = FakeClock()
    cb = CircuitBreaker("csmp:mcp_degraded:rag_kb", fail_max=1, reset_timeout=5, clock=clock)
    await _trip_open(cb)

    for _ in range(3):
        await cb.record_success()
        await cb.record_failure(RuntimeError("late"))
    assert cb.state == BreakerState.OPEN.value
    assert cb.failures == cb.fail_max
