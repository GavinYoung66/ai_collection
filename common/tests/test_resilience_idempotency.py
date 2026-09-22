"""common.resilience.idempotency 单测：首次占用/重复拒绝/Redis 挂保守拒绝。"""
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from common.resilience._redis import configure_redis, reset_redis
from common.resilience.idempotency import acquire


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.store: dict[str, tuple[str, int]] = {}
        self.set_calls = []

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if self.fail:
            raise RedisConnectionError("redis down")
        if key in self.store:
            return None
        self.store[key] = (value, ex)
        return True


@pytest.fixture(autouse=True)
def _reset_client():
    reset_redis()
    yield
    reset_redis()


@pytest.mark.asyncio
async def test_first_acquisition_succeeds_with_ttl():
    fake = FakeRedis()
    configure_redis(fake)

    assert await acquire("complaint:judgment:ticket123", ttl=1800) is True
    assert fake.store["complaint:judgment:ticket123"] == ("1", 1800)


@pytest.mark.asyncio
async def test_duplicate_acquisition_rejected():
    fake = FakeRedis()
    configure_redis(fake)
    key = "complaint:judgment:ticket123"

    assert await acquire(key) is True
    assert await acquire(key) is False
    assert await acquire(key) is False  # 重试同样拒绝，防重复写入


@pytest.mark.asyncio
async def test_redis_down_conservative_reject():
    configure_redis(FakeRedis(fail=True))

    assert await acquire("complaint:judgment:ticket999") is False
