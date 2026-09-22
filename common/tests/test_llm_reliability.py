"""common.llm.reliability 单测。"""
import asyncio

import pytest

from common.llm import reliability as reliability_mod
from common.llm.reliability import (
    RedisDegradeTracker,
    call_with_retry,
    invoke_llm,
    is_placeholder_secret,
    require_real_secret,
    resolve_service_name,
    reset_llm_transport,
    set_default_service,
    set_llm_transport,
)
from common.resilience._redis import configure_redis, reset_redis
from common.resilience.timeout import Timeout


def test_placeholder_secrets():
    assert is_placeholder_secret("change_me")
    assert is_placeholder_secret("")
    assert not is_placeholder_secret("sk-real")
    with pytest.raises(RuntimeError, match="placeholder"):
        require_real_secret("change_me", label="LLM_API_KEY")


@pytest.mark.asyncio
async def test_call_with_retry_succeeds_second_try():
    n = {"i": 0}

    async def flaky():
        n["i"] += 1
        if n["i"] < 2:
            raise RuntimeError("boom")
        return "ok"

    assert await call_with_retry(flaky, max_attempts=3, delay=0.01) == "ok"
    assert n["i"] == 2


@pytest.mark.asyncio
async def test_redis_degrade_success_clears_local_count():
    store: dict[str, str] = {}

    class FakeRedis:
        async def exists(self, key):
            return 1 if key in store else 0

        async def setex(self, key, ttl, value):
            store[key] = value

        async def delete(self, key):
            store.pop(key, None)

    tracker = RedisDegradeTracker(
        FakeRedis(), key="rag:llm:degraded", failure_threshold=3, ttl_seconds=60
    )
    await tracker.on_failure()
    await tracker.on_failure()
    await tracker.on_success()
    await tracker.on_failure()
    await tracker.on_failure()
    assert await tracker.is_degraded() is False
    await tracker.on_failure()
    assert await tracker.is_degraded() is True
    await tracker.on_success()
    assert await tracker.is_degraded() is False


@pytest.mark.asyncio
async def test_redis_degrade_prefix_isolation():
    store: dict[str, str] = {}

    class FakeRedis:
        async def exists(self, key):
            return 1 if key in store else 0

        async def setex(self, key, ttl, value):
            store[key] = value

        async def delete(self, key):
            store.pop(key, None)

    rag = RedisDegradeTracker(FakeRedis(), key="rag:llm:degraded", failure_threshold=2, ttl_seconds=60)
    cs = RedisDegradeTracker(FakeRedis(), key="csmp:llm_degraded", failure_threshold=2, ttl_seconds=60)

    await rag.on_failure()
    await rag.on_failure()
    assert await rag.is_degraded() is True
    assert await cs.is_degraded() is False


# ---------------------------------------------------------------------------
# invoke_llm 编排链
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):
        return None

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _isolate():
    """服务名/Redis/熔断注册表/transport 全隔离；yield 返回 fake 供断言。"""
    fake = FakeRedis()
    configure_redis(fake)
    set_default_service("csmp")
    reliability_mod._llm_breakers.clear()
    reset_llm_transport()
    yield fake
    set_default_service(None)
    reset_redis()
    reset_llm_transport()


@pytest.mark.asyncio
async def test_invoke_llm_primary_fail_fallback_succeeds(monkeypatch, _isolate):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

    async def transport(model, messages, timeout_seconds):
        if model == "deepseek-chat":
            raise RuntimeError("boom")
        return f"answer from {model}"

    set_llm_transport(transport)

    result = await invoke_llm(
        "deepseek-chat",
        [{"role": "user", "content": "hi"}],
        fallback_chain=["deepseek-chat-backup"],
    )

    assert result["model"] == "deepseek-chat-backup"
    assert result["content"] == "answer from deepseek-chat-backup"
    assert result["fallback_used"] is True
    assert result["degraded"] is True
    # spec：primary 失败即置位降级标志
    assert _isolate.store["csmp:llm_degraded:deepseek-chat"] == "1"


@pytest.mark.asyncio
async def test_invoke_llm_timeout_not_retried(_isolate):
    calls: dict[str, int] = {}

    async def transport(model, messages, timeout_seconds):
        calls[model] = calls.get(model, 0) + 1
        if model == "deepseek-chat":
            await asyncio.sleep(0.05)  # 超过 total=0.01
        return f"answer from {model}"

    set_llm_transport(transport)

    result = await invoke_llm(
        "deepseek-chat",
        [{"role": "user", "content": "hi"}],
        fallback_chain=["backup"],
        timeout=Timeout(total=0.01),
    )

    assert result["model"] == "backup"
    assert calls["deepseek-chat"] == 1  # 超时不重试，直接换 fallback


@pytest.mark.asyncio
async def test_invoke_llm_all_fail_degrade_flag_set(monkeypatch, _isolate):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
    calls: list[str] = []

    async def transport(model, messages, timeout_seconds):
        calls.append(model)
        raise RuntimeError(f"{model} down")

    set_llm_transport(transport)

    result = await invoke_llm("deepseek-chat", [], fallback_chain=["backup"])

    assert result["content"] is None  # 不返回伪造结果
    assert result["degraded"] is True
    assert result["fallback_used"] is True
    assert "deepseek-chat down" in result["error"]
    assert "csmp:llm_degraded:deepseek-chat" in _isolate.store
    # 业务错有界重试：primary 3 次 + fallback 3 次
    assert calls.count("deepseek-chat") == 3
    assert calls.count("backup") == 3


@pytest.mark.asyncio
async def test_invoke_llm_business_error_bounded_retry(monkeypatch, _isolate):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
    n = {"i": 0}

    async def transport(model, messages, timeout_seconds):
        n["i"] += 1
        if n["i"] < 3:
            raise RuntimeError("transient")
        return "ok"

    set_llm_transport(transport)

    result = await invoke_llm("deepseek-chat", [])

    assert result == {
        "model": "deepseek-chat",
        "content": "ok",
        "fallback_used": False,
        "degraded": False,
        "error": None,
    }
    assert n["i"] == 3


@pytest.mark.asyncio
async def test_invoke_llm_rate_limit_rejected(monkeypatch, _isolate):
    monkeypatch.setattr(reliability_mod, "_LLM_RATE_LIMIT", 0)

    async def transport(model, messages, timeout_seconds):
        return "x"

    set_llm_transport(transport)

    # 类型化异常：服务层可据此映射明确 error_type；且仍是 RuntimeError 子类（向后兼容）
    with pytest.raises(reliability_mod.LLMRateLimitError, match="rate limit"):
        await invoke_llm("deepseek-chat", [])


@pytest.mark.asyncio
async def test_invoke_llm_circuit_open_skips_to_fallback(_isolate):
    primary = reliability_mod._llm_breakers.get_or_create("csmp:llm_degraded:dead")
    for _ in range(3):
        await primary.record_failure()
    calls: list[str] = []

    async def transport(model, messages, timeout_seconds):
        calls.append(model)
        return f"ok {model}"

    set_llm_transport(transport)

    result = await invoke_llm("dead", [], fallback_chain=["alive"])

    assert result["model"] == "alive"
    assert calls == ["alive"]  # primary 熔断 open：不触达，直接 fallback


@pytest.mark.asyncio
async def test_invoke_llm_breaker_key_explicit(_isolate):
    async def transport(model, messages, timeout_seconds):
        raise RuntimeError("down")

    set_llm_transport(transport)

    result = await invoke_llm("m1", [], breaker_key="rag:llm_degraded:custom")

    assert result["degraded"] is True
    assert "rag:llm_degraded:custom" in _isolate.store


@pytest.mark.asyncio
async def test_resolve_service_name_unconfigured_raises():
    set_default_service(None)
    with pytest.raises(RuntimeError, match="服务名未配置"):
        resolve_service_name()


def test_llm_total_exceeds_mcp_total():
    # 超时层级约束：LLM total(120s) > MCP total(30s)
    assert Timeout().total > Timeout(total=30.0).total
