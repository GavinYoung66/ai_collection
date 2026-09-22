"""五维限流 P0 零件拼装单测（spec: complaint-service/rate-limit，tasks 4.3）。

覆盖：
- 50 并发 → 20 在跑 + 30 排队（维1 Semaphore + 维2 BoundedQueue）
- 队列满 → outcome=full（路由层映射 B0006/429，非 500、不转人审）
- 同 complaint_id 重复提交 → duplicate（维5 idempotency 锁）
- Redis 挂 → 限流维度静默放行（幂等锁经 ping 区分后降级放行）
- 维4 动态消费：深度 >250 x2、>400 x3；排队超 10min 进 expired
- 维3 MCP server 分桶 qps 窗口
- 用户维 key：X-User-Id 优先回退 IP
"""
from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.service.rate_limiter import (
    OUTCOME_DUPLICATE,
    OUTCOME_FULL,
    OUTCOME_GRANTED,
    OUTCOME_QUEUED,
    RateLimiter,
)
from app.utils.redis_keys import queue_start_key
from common.resilience._redis import configure_redis, reset_redis


class FakeRedis:
    """内存 Redis：支持 set(nx,ex)/get/delete/ping/incr/expire；fail=True 全抛连接错。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.store: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.deleted: list[str] = []

    async def _raise_if_down(self) -> None:
        if self.fail:
            raise RedisConnectionError("redis down")

    async def set(self, key, value, nx: bool = False, ex: int | None = None):  # noqa: ARG002
        await self._raise_if_down()
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def get(self, key):
        await self._raise_if_down()
        return self.store.get(key)

    async def delete(self, *keys):
        await self._raise_if_down()
        n = 0
        for k in keys:
            self.deleted.append(k)
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    async def ping(self):
        await self._raise_if_down()
        return True

    async def incr(self, key: str) -> int:
        await self._raise_if_down()
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):  # noqa: ARG002
        await self._raise_if_down()


def _cfg(max_active: int = 20, queue_max: int = 500, base_rate: int = 5) -> dict:
    return {
        "complaint": {
            "max_active": max_active,
            "queue_max": queue_max,
            "queue_timeout_minutes": 10,
            "exec_timeout_seconds": 1800,
            "lock_ttl_seconds": 1800,
        },
        "mcp": {"qps": {"business_ticket": 50, "call_qc": 30, "rag_kb": 20, "data_platform": 30}},
        "user": {"qps": 200, "window": 1},
        "queue": {"dynamic_consume": True, "base_consume_rate": base_rate},
    }


@pytest.fixture(autouse=True)
def _isolate_redis():
    reset_redis()
    yield
    reset_redis()


@pytest.mark.asyncio
async def test_50_concurrent_20_running_30_queued():
    """50 条并发：20 granted 在跑，30 queued 排队（页面可展示排队状态）。"""
    fake = FakeRedis()
    r = RateLimiter(config=_cfg(), redis_client=fake)

    outcomes = [await r.acquire_complaint(f"c{i}") for i in range(50)]

    assert outcomes.count(OUTCOME_GRANTED) == 20
    assert outcomes.count(OUTCOME_QUEUED) == 30
    assert r.inflight == 20
    assert r.queue_depth == 30
    # 入队即写 queue_start（10min TTL）
    assert fake.store.get(queue_start_key("c20")) is not None


@pytest.mark.asyncio
async def test_queue_full_returns_full_for_b0006():
    """维2 队列满 → full（路由层据此产 B0006/429），不转人审。"""
    r = RateLimiter(config=_cfg(max_active=1, queue_max=2), redis_client=FakeRedis())

    assert await r.acquire_complaint("running") == OUTCOME_GRANTED
    assert await r.acquire_complaint("q1") == OUTCOME_QUEUED
    assert await r.acquire_complaint("q2") == OUTCOME_QUEUED
    assert await r.acquire_complaint("q3") == OUTCOME_FULL


@pytest.mark.asyncio
async def test_duplicate_complaint_id_rejected():
    """维5 同 complaint_id 并发提交两次 → 第二次 duplicate。"""
    r = RateLimiter(config=_cfg(), redis_client=FakeRedis())

    assert await r.acquire_complaint("dup") == OUTCOME_GRANTED
    assert await r.acquire_complaint("dup") == OUTCOME_DUPLICATE


@pytest.mark.asyncio
async def test_redis_down_silently_allows():
    """Redis 不可达：幂等锁降级放行（不同工单均 granted），MCP/用户维 allow 放行。"""
    r = RateLimiter(config=_cfg(), redis_client=FakeRedis(fail=True))

    assert await r.acquire_complaint("x1") == OUTCOME_GRANTED
    assert await r.acquire_complaint("x2") == OUTCOME_GRANTED  # 非 duplicate，放行
    assert await r.acquire_mcp("rag_kb") is True
    assert await r.acquire_user("user-a") is True


@pytest.mark.asyncio
async def test_dim4_dynamic_consume_rates():
    """维4：深度 >250 消费 x2（10/批）、>400 消费 x3（15/批）。"""
    # x2：占满 20 槽再排 251 单（深度 251>250），消费前释放槽位
    fake = FakeRedis()
    r = RateLimiter(config=_cfg(max_active=20, queue_max=500, base_rate=5),
                    redis_client=fake)
    for i in range(20):
        assert await r.acquire_complaint(f"hold{i}") == OUTCOME_GRANTED
    for i in range(251):
        assert await r.acquire_complaint(f"d{i}") == OUTCOME_QUEUED
    for _ in range(20):
        r.release_complaint()

    ready, expired = await r.consume_pending(max_n=5)
    assert len(ready) == 10 and expired == []

    # x3：另起干净实例，占 20 槽 + 排 401 单（深度 401>400）
    fake2 = FakeRedis()
    r2 = RateLimiter(config=_cfg(max_active=20, queue_max=500, base_rate=5),
                     redis_client=fake2)
    for i in range(20):
        assert await r2.acquire_complaint(f"hold{i}") == OUTCOME_GRANTED
    for i in range(401):
        assert await r2.acquire_complaint(f"e{i}") == OUTCOME_QUEUED
    for _ in range(20):
        r2.release_complaint()
    ready2, _ = await r2.consume_pending(max_n=5)
    assert len(ready2) == 15


@pytest.mark.asyncio
async def test_consume_expired_queue_item():
    """排队超 10min（queue_start 已失效）→ 弹出进 expired（调用方转 A5010 人审）。"""
    fake = FakeRedis()
    r = RateLimiter(config=_cfg(max_active=1, queue_max=10), redis_client=fake)
    await r.acquire_complaint("hold")
    assert await r.acquire_complaint("late") == OUTCOME_QUEUED
    # 模拟 TTL 到期
    await fake.delete(queue_start_key("late"))

    r.release_complaint()
    ready, expired = await r.consume_pending(max_n=5)
    assert ready == [] and expired == ["late"]


@pytest.mark.asyncio
async def test_mcp_server_token_bucket_per_server():
    """维3 rag_kb server qps=20：窗口内第 21 次拒绝；未配置 server 放行。"""
    r = RateLimiter(config=_cfg(), redis_client=FakeRedis())
    for _ in range(20):
        assert await r.acquire_mcp("rag_kb") is True
    assert await r.acquire_mcp("rag_kb") is False
    assert await r.acquire_mcp("unknown_server") is True


@pytest.mark.asyncio
async def test_user_key_resolution():
    """用户维 key：X-User-Id 优先，缺失回退 IP，再缺匿名。"""
    resolve = RateLimiter.resolve_user_key
    assert resolve({"X-User-Id": "u1"}, "1.2.3.4") == "u1"
    assert resolve({}, "1.2.3.4") == "1.2.3.4"
    assert resolve(None, None) == "anonymous"
