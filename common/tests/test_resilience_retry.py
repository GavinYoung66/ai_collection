"""T28：统一重试原语的行为与收敛约束。"""
from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from common.llm.reliability.retry import call_with_retry
from common.resilience import retry as retry_pkg
from common.resilience.retry import (
    DEFAULT_RETRY,
    EVENTBUS_RETRY,
    LLM_RETRY,
    MCP_RETRY,
    RetryPolicy,
    retry_async,
    retry_delay,
)


class _SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def sleep_recorder(monkeypatch):
    rec = _SleepRecorder()
    monkeypatch.setattr(retry_pkg, "asyncio", SimpleNamespace(sleep=rec))
    return rec


async def test_retry_async_success_first_attempt_no_sleep(sleep_recorder):
    calls = {"n": 0}

    async def ok():
        calls["n"] += 1
        return "ok"

    assert await retry_async(ok, policy=RetryPolicy(max_attempts=3)) == "ok"
    assert calls["n"] == 1
    assert sleep_recorder.delays == []


async def test_retry_async_retries_then_succeeds(sleep_recorder):
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = await retry_async(
        flaky, policy=RetryPolicy(max_attempts=3, base_delay=1.0, backoff=2.0)
    )
    assert result == "ok"
    assert calls["n"] == 3
    assert sleep_recorder.delays == [1.0, 2.0]


async def test_retry_async_exhaustion_raises_last_error(sleep_recorder):
    calls = {"n": 0}

    async def fail():
        calls["n"] += 1
        raise ValueError(f"boom-{calls['n']}")

    with pytest.raises(ValueError, match="boom-4"):
        await retry_async(fail, policy=RetryPolicy(max_attempts=4))
    assert calls["n"] == 4
    assert sleep_recorder.delays == [1.0, 2.0, 4.0]


async def test_retry_async_max_attempts_one_is_boundary(sleep_recorder):
    async def fail():
        raise RuntimeError("once")

    with pytest.raises(RuntimeError, match="once"):
        await retry_async(fail, policy=RetryPolicy(max_attempts=1))
    assert sleep_recorder.delays == []


async def test_retry_async_should_retry_false_stops_immediately(sleep_recorder):
    calls = {"n": 0}

    async def permission_denied():
        calls["n"] += 1
        raise PermissionError("no")

    with pytest.raises(PermissionError):
        await retry_async(
            permission_denied,
            policy=RetryPolicy(max_attempts=3),
            should_retry=lambda _exc: False,
        )
    assert calls["n"] == 1
    assert sleep_recorder.delays == []


async def test_retry_async_on_retry_reports_final_zero_delay(sleep_recorder):
    seen: list[tuple[int, str, float]] = []

    async def fail():
        raise RuntimeError("always")

    async def on_retry(attempt: int, exc: BaseException, delay: float) -> None:
        seen.append((attempt, type(exc).__name__, delay))

    with pytest.raises(RuntimeError):
        await retry_async(fail, policy=RetryPolicy(max_attempts=3), on_retry=on_retry)
    assert seen == [(1, "RuntimeError", 1.0), (2, "RuntimeError", 2.0), (3, "RuntimeError", 0.0)]


async def test_retry_async_undeclared_exception_propagates(sleep_recorder):
    calls = {"n": 0}

    async def value_error():
        calls["n"] += 1
        raise ValueError("not declared")

    with pytest.raises(ValueError):
        await retry_async(
            value_error,
            policy=RetryPolicy(max_attempts=3),
            exceptions=(RuntimeError,),
        )
    assert calls["n"] == 1


def test_retry_delay_caps_and_validates():
    assert retry_delay(1, EVENTBUS_RETRY) == 1.0
    assert retry_delay(2, EVENTBUS_RETRY) == 2.0
    assert retry_delay(4, EVENTBUS_RETRY) == 8.0
    assert retry_delay(5, EVENTBUS_RETRY) == 8.0
    with pytest.raises(ValueError):
        retry_delay(0, EVENTBUS_RETRY)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay": -1},
        {"backoff": 0.5},
        {"max_delay": -1},
    ],
)
def test_retry_policy_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_policies_are_single_source() -> None:
    """四处重试的默认值与命名策略保持唯一配置源。"""
    assert LLM_RETRY.max_attempts == 3
    assert MCP_RETRY.base_delay == 0.5
    assert EVENTBUS_RETRY.max_attempts == 4
    assert DEFAULT_RETRY == LLM_RETRY


async def test_llm_call_with_retry_delegates_to_unified_primitive(sleep_recorder):
    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await call_with_retry(always_fail, max_attempts=4, delay=1.0, backoff=2.0)
    assert calls["n"] == 4
    assert sleep_recorder.delays == [1.0, 2.0, 4.0]


def test_utils_retry_is_compatibility_reexport() -> None:
    from common.resilience.retry import retry as canonical
    from common.utils.retry import retry as utils_retry

    assert utils_retry is canonical


def test_policies_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_RETRY.max_attempts = 5  # type: ignore[misc]


async def test_zero_delay_policy_skips_sleep(sleep_recorder):
    async def flaky():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await retry_async(flaky, policy=RetryPolicy(max_attempts=2, base_delay=0))
    assert sleep_recorder.delays == []


def test_asyncio_import_available_for_patch_point() -> None:
    assert asyncio.sleep is not None
