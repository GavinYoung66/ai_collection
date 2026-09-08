"""common.llm.reliability 单测。"""
import pytest

from common.llm.reliability import (
    RedisDegradeTracker,
    call_with_retry,
    is_placeholder_secret,
    require_real_secret,
)


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
