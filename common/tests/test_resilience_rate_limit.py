"""common.resilience.rate_limit 单测：限流放行/超限拒绝/Redis 挂静默放行/事件上报。"""
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from common.resilience._redis import configure_redis, reset_redis
from common.resilience.hooks import set_publisher
from common.resilience.rate_limit import allow


class FakeRedis:
    """记录 SET NX EX / INCR 调用的内存 Redis，fail=True 模拟 Redis 不可用。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.incr_calls: list[str] = []

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if self.fail:
            raise RedisConnectionError("redis down")
        if nx and key in self.counts:
            return None
        self.set_calls.append((key, value, ex))
        self.counts[key] = int(value)
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RedisConnectionError("redis down")
        self.incr_calls.append(key)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, window: int) -> None:
        if self.fail:
            raise RedisConnectionError("redis down")


@pytest.fixture(autouse=True)
def _reset_client():
    reset_redis()
    yield
    reset_redis()


@pytest.fixture
def events():
    """收集 on_event 事件（注入本 fixture 的测试才启用 publisher）。"""
    collected = []

    async def publisher(name, payload):
        collected.append((name, payload))

    set_publisher(publisher)
    yield collected
    set_publisher(None)


@pytest.mark.asyncio
async def test_under_limit_allows_and_increments():
    fake = FakeRedis()
    configure_redis(fake)
    key = "rag:ratelimit:kb_upload:user42"

    for _ in range(10):
        assert await allow(key, limit=20, window=60) is True

    assert fake.counts[key] == 10
    # SET NX EX 只在首调（键不存在）时执行一次，保证计数键带 TTL；之后仅 INCR
    assert fake.set_calls == [(key, "0", 60)]
    assert fake.ttls[key] == 60
    assert len(fake.incr_calls) == 10


@pytest.mark.asyncio
async def test_over_limit_denied_and_emits_hit_event(events):
    fake = FakeRedis()
    configure_redis(fake)
    key = "rag:ratelimit:kb_upload:user42"

    results = [await allow(key, limit=20, window=60) for _ in range(21)]

    assert results[:20] == [True] * 20
    assert results[20] is False  # 第 21 次超限 → False
    assert fake.counts[key] == 21
    # spec Scenario：超限（返回 False）须发 {svc}.rate_limit.hit 事件
    assert events == [(
        "rag.rate_limit.hit",
        {"key": key, "limit": 20, "reason": "rate_limited"},
    )]


@pytest.mark.asyncio
async def test_under_limit_emits_no_event(events):
    fake = FakeRedis()
    configure_redis(fake)

    assert await allow("rag:ratelimit:user1", limit=20) is True

    assert events == []  # 未超限不发事件


@pytest.mark.asyncio
async def test_redis_down_silent_allow_and_emits_degraded_event(events):
    configure_redis(FakeRedis(fail=True))

    assert await allow("any:key", limit=1, window=60) is True

    # design Risk 缓解：限流降级发 {svc}.rate_limit.degraded 供事后告警
    assert events == [
        ("any.rate_limit.degraded", {"key": "any:key", "error": "redis down"})
    ]


@pytest.mark.asyncio
async def test_different_keys_count_independently():
    fake = FakeRedis()
    configure_redis(fake)

    assert await allow("k:a", limit=1) is True
    assert await allow("k:b", limit=1) is True
    assert await allow("k:a", limit=1) is False
    assert await allow("k:b", limit=1) is False


@pytest.mark.asyncio
async def test_set_nx_failure_does_not_leave_bare_incr_key(events):
    """P3-5 崩溃安全回归：SET NX EX 失败时 MUST NOT 再 INCR（杜绝无 TTL 计数键）。

    旧实现 INCR 成功后补 EXPIRE，两命令间断连会留下永不过期键，恢复后永久限流。
    """

    class _SetFailsRedis(FakeRedis):
        async def set(self, key, value, ex=None, nx=False):
            raise RedisConnectionError("connection dropped before SET")

    fake = _SetFailsRedis()
    configure_redis(fake)

    # Redis 故障 → fail-open 放行，并发 degraded 事件
    assert await allow("csmp:llm_ratelimit:m1", limit=1, window=60) is True
    # INCR 绝不能在 SET 失败后执行：没有裸计数键产生
    assert fake.incr_calls == []
    assert events == [
        (
            "csmp.rate_limit.degraded",
            {"key": "csmp:llm_ratelimit:m1", "error": "connection dropped before SET"},
        )
    ]
