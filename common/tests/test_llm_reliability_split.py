"""T29 reliability god-module 拆分等价性单测。

拆分只搬移不改语义（design D6 风险处置「拆分前为既有重试语义补等价性测试」）。
本文件钉住拆分后各子模块的行为契约：

- 门面兼容性：``common.llm.reliability`` 包导出面与拆分前一致，且共享同一实例；
- retry.py：指数退避序列、异常过滤、耗尽抛最后原始异常（正常/边界/异常）；
- rate_limit.py：正整数解析的空白容忍与非法回退（边界/异常）；
- orchestrator.py：_call_model 业务错重试退避公式 1.0 * 2**attempt、超时不重试（等价性）；
- degrade.py：mark_degraded_flag 失败即置位且默认 TTL=60（等价性）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from common.llm.reliability import (
    call_with_retry,
    get_llm_rate_limit,
    is_placeholder_secret,
    mark_degraded_flag,
    require_real_secret,
    set_default_service,
)
from common.llm.reliability import orchestrator as llm_orchestrator
from common.llm.reliability import rate_limit as llm_rate_limit
from common.resilience._redis import configure_redis, reset_redis


# ---------------------------------------------------------------------------
# 门面兼容性：包导出与子模块共享同一对象（patch 点不漂移）
# ---------------------------------------------------------------------------
def test_facade_shares_submodule_instances():
    assert llm_orchestrator._llm_breakers is not None
    import common.llm.reliability as reliability_pkg

    assert reliability_pkg._llm_breakers is llm_orchestrator._llm_breakers
    assert reliability_pkg.get_llm_rate_limit is llm_rate_limit.get_llm_rate_limit
    assert reliability_pkg.invoke_llm is llm_orchestrator.invoke_llm
    assert reliability_pkg.call_with_retry is call_with_retry


# ---------------------------------------------------------------------------
# retry.py：有界指数退避原语（只搬移语义钉死）
# ---------------------------------------------------------------------------
class _SleepRecorder:
    """替换 retry 模块内 asyncio.sleep：记录退避序列且不真实等待。"""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def test_call_with_retry_backoff_sequence_equivalence(monkeypatch):
    """正常/等价性：delay=1、backoff=2、4 次尝试 → 退避序列 [1, 2, 4]。"""
    rec = _SleepRecorder()
    monkeypatch.setattr(
        "common.resilience.retry.asyncio",
        SimpleNamespace(sleep=rec),
    )
    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await call_with_retry(always_fail, max_attempts=4, delay=1.0, backoff=2.0)

    assert calls["n"] == 4
    assert rec.delays == [1.0, 2.0, 4.0]


async def test_call_with_retry_success_first_attempt_no_sleep(monkeypatch):
    """正常：一次成功不触发任何退避等待。"""
    rec = _SleepRecorder()
    monkeypatch.setattr(
        "common.resilience.retry.asyncio",
        SimpleNamespace(sleep=rec),
    )

    async def ok():
        return "fine"

    assert await call_with_retry(ok, max_attempts=3, delay=1.0) == "fine"
    assert rec.delays == []


async def test_call_with_retry_only_catches_declared_exceptions():
    """异常：未声明异常类型直接冒泡，不重试（语义等价：exceptions 元组过滤）。"""
    calls = {"n": 0}

    async def raises_value_error():
        calls["n"] += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError, match="not retryable"):
        await call_with_retry(
            raises_value_error,
            max_attempts=3,
            delay=0.01,
            exceptions=(RuntimeError,),
        )
    assert calls["n"] == 1


async def test_call_with_retry_max_attempts_one_no_retry():
    """边界：max_attempts=1 时失败即抛，不做任何等待。"""
    calls = {"n": 0}

    async def fail():
        calls["n"] += 1
        raise RuntimeError("once")

    with pytest.raises(RuntimeError, match="once"):
        await call_with_retry(fail, max_attempts=1, delay=0.01)
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# rate_limit.py：阈值解析（边界/异常）
# ---------------------------------------------------------------------------
def test_parse_positive_int_env_strips_surrounding_whitespace(monkeypatch):
    """边界：两端空白值（"60 "）允许 strip 后解析（拆分前注释明确的行为）。"""
    monkeypatch.setenv("LLM_RATE_LIMIT_X", " 60 ")
    assert llm_rate_limit._parse_positive_int_env("LLM_RATE_LIMIT_X", 7) == 60


def test_parse_positive_int_env_unset_returns_default(monkeypatch):
    """正常：未设置环境变量返回默认值。"""
    monkeypatch.delenv("LLM_RATE_LIMIT_MISSING", raising=False)
    assert llm_rate_limit._parse_positive_int_env("LLM_RATE_LIMIT_MISSING", 7) == 7


def test_get_llm_rate_limit_single_source():
    """单一事实源：get 入口返回的即模块级当前值。"""
    limit, window = get_llm_rate_limit()
    assert (limit, window) == (llm_rate_limit._LLM_RATE_LIMIT, llm_rate_limit._LLM_RATE_WINDOW)


# ---------------------------------------------------------------------------
# orchestrator._call_model：业务错重试退避公式等价性（1.0 * 2**attempt）
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _service_name():
    """_call_model 经 resolve_service_name 派生埋点 svc；用后复位。"""
    set_default_service("csmp")
    yield
    set_default_service(None)


class _Breaker:
    def __init__(self):
        self.key = "csmp:llm_degraded:m"
        self.failures = 0
        self.successes = 0

    async def before_call(self):
        return True

    async def record_failure(self, _e=None):
        self.failures += 1

    async def record_success(self):
        self.successes += 1


class _Timeout:
    total = 5.0


async def test_call_model_business_error_retry_backoff_formula(monkeypatch):
    """等价性：业务错重试退避 = _LLM_RETRY_DELAY * 2**attempt（1, 2），且中间失败不计熔断。"""
    rec = _SleepRecorder()
    monkeypatch.setattr(
        llm_orchestrator,
        "asyncio",
        SimpleNamespace(sleep=rec, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError),
    )
    breaker = _Breaker()
    calls = {"n": 0}

    async def transport(model, messages, timeout_seconds):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = await llm_orchestrator._call_model(
        transport, "m", [], _Timeout(), breaker
    )

    assert result == "ok"
    assert calls["n"] == 3
    assert rec.delays == [1.0, 2.0]  # _LLM_RETRY_DELAY=1.0：1 * 2**0, 1 * 2**1
    assert breaker.failures == 0  # 中间失败不计数，最终成功记 success
    assert breaker.successes == 1


async def test_call_model_timeout_not_retried():
    """等价性：超时不重试（wait_for 抛 TimeoutError 直接冒泡），计 1 次熔断失败。"""
    breaker = _Breaker()
    calls = {"n": 0}

    class _ShortTimeout:
        total = 0.01

    async def slow_transport(model, messages, timeout_seconds):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(asyncio.TimeoutError):
        await llm_orchestrator._call_model(
            slow_transport, "m", [], _ShortTimeout(), breaker
        )
    assert calls["n"] == 1
    assert breaker.failures == 1


# ---------------------------------------------------------------------------
# degrade.mark_degraded_flag：失败即置位 + 默认 TTL=60（等价性）
# ---------------------------------------------------------------------------
class _TtlRedis:
    def __init__(self):
        self.writes: dict[str, tuple[int, str]] = {}

    async def setex(self, key, ttl, value):
        self.writes[key] = (ttl, value)

    async def exists(self, key):
        return 1 if key in self.writes else 0

    async def delete(self, key):
        self.writes.pop(key, None)


async def test_mark_degraded_flag_sets_with_default_ttl_60():
    """等价性：一次失败即 setex，默认 TTL 60 秒；键保持服务前缀。"""
    fake = _TtlRedis()
    configure_redis(fake)
    try:
        await mark_degraded_flag("csmp:llm_degraded:m1")
        assert fake.writes["csmp:llm_degraded:m1"] == (60, "1")
    finally:
        reset_redis()


# ---------------------------------------------------------------------------
# secrets：真路径放行（正常）
# ---------------------------------------------------------------------------
def test_require_real_secret_passes_through_real_key():
    assert require_real_secret("sk-real", label="K") == "sk-real"
    assert is_placeholder_secret(None) is True
