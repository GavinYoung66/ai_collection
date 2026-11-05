"""W4：common.resilience._redis.aclose_redis() shutdown 出口单测。

验收判据：
- 缓存客户端有 aclose（redis.asyncio.Redis）→ 被调用恰好一次且引用清空；
- 无 aclose 但有 close 的替身 → close 兜底被调用；
- 无任何关闭出口的替身（测试桩）→ 静默通过；
- aclose 抛异常 → warning 留痕不阻断（T40）；
- 空缓存 → no-op。

依赖：PYTHONPATH=.:$PYTHONPATH
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from common.resilience import _redis as redis_mod
from common.resilience._redis import aclose_redis, configure_redis, reset_redis


class _CloseOnlyClient:
    def __init__(self):
        self.close = AsyncMock()


class _StubClient:
    """无任何关闭出口（部分测试替身形态）。"""


class _BrokenClient:
    def __init__(self):
        self.aclose = AsyncMock(side_effect=RuntimeError("pool broken"))


@pytest.fixture(autouse=True)
def _reset():
    reset_redis()
    yield
    reset_redis()


@pytest.mark.asyncio
async def test_aclose_redis_closes_cached_client_once():
    fake = AsyncMock()
    configure_redis(fake)

    await aclose_redis()

    fake.aclose.assert_awaited_once()
    assert redis_mod._client is None


@pytest.mark.asyncio
async def test_aclose_redis_falls_back_to_close():
    fake = _CloseOnlyClient()
    configure_redis(fake)

    await aclose_redis()

    fake.close.assert_awaited_once()
    assert redis_mod._client is None


@pytest.mark.asyncio
async def test_aclose_redis_tolerates_client_without_close():
    configure_redis(_StubClient())

    await aclose_redis()  # 不抛

    assert redis_mod._client is None


@pytest.mark.asyncio
async def test_aclose_redis_failure_does_not_raise(monkeypatch):
    spy = type("S", (), {"warning": staticmethod(lambda *a, **k: None)})()
    monkeypatch.setattr(redis_mod, "logger", spy)
    configure_redis(_BrokenClient())

    await aclose_redis()  # 不抛

    assert redis_mod._client is None


@pytest.mark.asyncio
async def test_aclose_redis_noop_when_empty():
    await aclose_redis()  # 不抛、无副作用

    assert redis_mod._client is None
