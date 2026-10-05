"""阶段3：HTTP 队列满溢出 Redis 延迟队列单测（surge hardening 3.1-3.6，design D4）。

覆盖：
- 3.1 配置默认值：开关默认关、容量默认 10000、deferred key 命名空间
- 3.2 溢出接入：开关开 + 未满 → HTTP 202 + LPUSH；默认关 → B0006 不写 Redis
- 3.3 回退路径：容量满 / Redis 故障 → B0006（fail-closed）；offer/pop/requeue FIFO
- 3.4 补偿循环：granted 执行并释放 / queued 不重复触发 / duplicate 忽略 / full 退避回塞
- 3.5 关停不丢：在途 cid 取消后重塞回队列
- 3.6 端到端冒烟：闸+队列满溢出 → 202 → 资源释放后补位执行且每 cid 仅一次
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.events.publisher import publisher
from app.main import _deferred_consumer_loop, _queue_consumer_loop, app
from app.service.complaint_service import complaint_service
from app.service.rate_limiter import (
    OUTCOME_DUPLICATE,
    OUTCOME_FULL,
    OUTCOME_GRANTED,
    OUTCOME_QUEUED,
    rate_limiter,
)
from app.utils.redis_keys import deferred_queue_key, judge_lock_key
from common.resilience._redis import configure_redis
from common.resilience.concurrency import Semaphore
from common.resilience.queue import BoundedQueue
from tests.conftest import _GlobalFakeRedis


class _ListFakeRedis(_GlobalFakeRedis):
    """在 _GlobalFakeRedis 基础上补 List 命令（LPUSH/LLEN/BRPOP），溢出队列用。

    fail=True 时 List/存在性命令全抛连接错（模拟 Redis 不可达，fail-closed 用）。
    """

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.lists: dict[str, list[str]] = {}

    async def _raise_if_down(self) -> None:
        if self.fail:
            raise RedisConnectionError("redis down")

    async def lpush(self, key, value):
        await self._raise_if_down()
        self.lists.setdefault(key, []).insert(0, str(value))
        return len(self.lists[key])

    async def llen(self, key):
        await self._raise_if_down()
        return len(self.lists.get(key, []))

    async def brpop(self, key, timeout=0):  # noqa: ARG002 — fake 无阻塞语义
        await self._raise_if_down()
        lst = self.lists.get(key)
        if not lst:
            return None
        return (key, lst.pop())

    async def eval(self, script, numkeys, *keys_and_args):  # noqa: ARG002 — 镜像 Lua 语义
        """镜像 _DEFERRED_OFFER_LUA：LLEN→容量判定→LPUSH 原子完成。

        签名对齐 redis-py eval(script, numkeys, *keys_and_args)；
        返回入队后深度，容量满返回 -1（与生产脚本一致）。
        """
        await self._raise_if_down()
        key, max_n, value = keys_and_args[0], int(keys_and_args[1]), str(keys_and_args[2])
        depth = len(self.lists.get(key, []))
        if depth >= max_n:
            return -1
        self.lists.setdefault(key, []).insert(0, value)
        return depth + 1


class _FakeLoopLimiter:
    """补偿循环专用 fake 限流器：可控 pop 序列 + 接入结果 + 释放/重塞记录。"""

    def __init__(self, pops, outcomes) -> None:
        self._pops = list(pops)
        self._outcomes = dict(outcomes)
        self.pop_calls = 0
        self.requeued: list[str] = []
        self.released_locks: list[str] = []
        self.released_slots = 0
        self.requeue_delay = 0.0  # 重塞前置延迟：供测试在 requeue await 内注入二次 cancel

    async def deferred_pop(self, timeout: int = 5):  # noqa: ARG002
        self.pop_calls += 1
        return self._pops.pop(0) if self._pops else None

    async def acquire_complaint(self, cid: str):
        return self._outcomes.get(cid, OUTCOME_DUPLICATE)

    async def deferred_requeue(self, cid: str):
        if self.requeue_delay:
            await asyncio.sleep(self.requeue_delay)
        self.requeued.append(cid)

    async def release_complaint_lock(self, cid: str):
        self.released_locks.append(cid)

    def release_complaint(self):
        self.released_slots += 1


def _reset_limiter(
    max_active: int = 20,
    queue_max: int = 500,
    fake=None,
    deferred_max: int | None = None,
):
    """重建限流器单例内部状态（并发槽/队列/成员表/兜底容量），测试隔离用。"""
    if fake is not None:
        configure_redis(fake)
    rate_limiter._max_active = max_active
    rate_limiter._queue_max = queue_max
    rate_limiter._sem = Semaphore(max_active)
    rate_limiter._queue = BoundedQueue(queue_max)
    rate_limiter._queued.clear()
    if deferred_max is not None:
        rate_limiter._deferred_max = deferred_max
    return rate_limiter


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _isolate():
    publisher.clear()
    complaint_service._cache.clear()
    yield
    _reset_limiter()
    publisher.clear()
    complaint_service._cache.clear()


# ── 3.1 配置默认值（默认关闭 = 现状不变）──


def test_default_settings_and_key():
    assert settings.COMPLAINT_DEFERRED_QUEUE_ENABLED is False
    assert settings.COMPLAINT_DEFERRED_QUEUE_MAX == 10000
    assert deferred_queue_key() == "csmp:complaint:deferred"


@pytest.fixture()
def _settings_max(monkeypatch):
    """隔离 COMPLAINT_DEFERRED_QUEUE_MAX，验证容量配置来源两分支语义。"""
    monkeypatch.setattr(settings, "COMPLAINT_DEFERRED_QUEUE_MAX", 12345)
    return settings


_MIN_RATE_LIMITER_CFG = {
    "complaint": {"max_active": 20, "queue_max": 500},
    "mcp": {"qps": {"business_ticket": 50}},
    "user": {"qps": 200, "window": 1},
    "queue": {},
}


def test_deferred_max_config_source(_settings_max):
    """兜底容量来源固化：yaml 无 deferred 段 → env/settings 值；提供 → yaml 覆盖。"""
    from app.service.rate_limiter import RateLimiter

    # 无 deferred 段（yaml 注释态/未注入）：env 驱动
    assert RateLimiter(config=dict(_MIN_RATE_LIMITER_CFG))._deferred_max == 12345
    # yaml/config 提供 deferred.max：覆盖 env（注释中已警示的劫持语义）
    cfg = dict(_MIN_RATE_LIMITER_CFG, deferred={"max": 7})
    assert RateLimiter(config=cfg)._deferred_max == 7


# ── 3.3 RateLimiter 延迟队列方法 ──


@pytest.mark.asyncio
async def test_deferred_offer_pop_fifo():
    """LPUSH 左进 + BRPOP 右出 = FIFO；pop 超时返回 None。"""
    fake = _ListFakeRedis()
    r = _reset_limiter(fake=fake)

    assert await r.deferred_offer("a") is True
    assert await r.deferred_offer("b") is True
    assert fake.lists[deferred_queue_key()] == ["b", "a"]  # 左进（b 后到在队头）
    assert await r.deferred_pop(timeout=0) == "a"  # 右出 FIFO：先入先出
    assert await r.deferred_pop(timeout=0) == "b"
    assert await r.deferred_pop(timeout=0) is None


@pytest.mark.asyncio
async def test_deferred_offer_capacity_full_returns_false():
    """兜底容量满 → offer False（路由层回退 B0006），不继续 LPUSH。"""
    fake = _ListFakeRedis()
    r = _reset_limiter(fake=fake, deferred_max=1)

    assert await r.deferred_offer("a") is True
    assert await r.deferred_offer("b") is False
    assert fake.lists[deferred_queue_key()] == ["a"]


@pytest.mark.asyncio
async def test_deferred_redis_down_fail_closed():
    """Redis 故障：offer False（fail-closed 回退 B0006）、pop None、requeue 不抛。"""
    r = _reset_limiter(fake=_ListFakeRedis(fail=True))

    assert await r.deferred_offer("a") is False
    assert await r.deferred_pop(timeout=0) is None
    await r.deferred_requeue("a")  # 仅 warn，不抛异常（循环不退出）
    await r.deferred_requeue("b")


# ── 3.2 / 3.3 路由级：full 分支溢出与回退 ──


async def _fill_to_full(rl) -> None:
    """服务级占满并发槽 + 内存队列（等价并发到达），使后续 HTTP 接入得 full。"""
    for i in range(rl._max_active):
        assert await rl.acquire_complaint(f"hold-{i}") == OUTCOME_GRANTED
    for i in range(rl._queue_max):
        assert await rl.acquire_complaint(f"q-{i}") == OUTCOME_QUEUED


@pytest.mark.asyncio
async def test_route_default_off_full_returns_b0006_no_redis_write(monkeypatch):
    """默认开关关闭：full → B0006，且不写 Redis 延迟队列（现状零变化）。"""
    monkeypatch.setattr(settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", False)
    fake = _ListFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=1, fake=fake)
    await _fill_to_full(rl)
    cid = complaint_service.create("默认关溢出投诉")["complaint_id"]

    async with _client() as ac:
        r = await ac.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": False})

    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "B0006"
    assert body["data"] is None
    assert deferred_queue_key() not in fake.lists  # 未写 Redis


@pytest.mark.asyncio
async def test_route_enabled_overflow_returns_202_and_lpush(monkeypatch):
    """开关开 + 未超容量：full → 202 + code=0 + LPUSH 入队，不返回 B0006。"""
    monkeypatch.setattr(settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", True)
    fake = _ListFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=1, fake=fake, deferred_max=10)
    await _fill_to_full(rl)
    cid = complaint_service.create("溢出受理投诉")["complaint_id"]

    async with _client() as ac:
        r = await ac.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": False})

    assert r.status_code == 202
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["message"] == "judge queued in deferred backlog"
    assert body["data"]["task_status"] == "pending"
    assert fake.lists[deferred_queue_key()] == [cid]  # 已入队
    # 维5锁已在 full 分支回滚：补偿循环重新接入不会被误判 duplicate
    assert judge_lock_key(cid) not in fake.store


@pytest.mark.asyncio
async def test_route_enabled_capacity_full_falls_back_b0006(monkeypatch):
    """开关开但兜底容量满 → 回退 B0006。"""
    monkeypatch.setattr(settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", True)
    fake = _ListFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=1, fake=fake, deferred_max=1)
    await _fill_to_full(rl)
    await fake.lpush(deferred_queue_key(), "占位")  # 容量 1 已被占
    cid = complaint_service.create("容量满投诉")["complaint_id"]

    async with _client() as ac:
        r = await ac.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": False})

    assert r.status_code == 200
    assert r.json()["code"] == "B0006"
    assert fake.lists[deferred_queue_key()] == ["占位"]  # 未新增


@pytest.mark.asyncio
async def test_route_enabled_redis_down_falls_back_b0006(monkeypatch):
    """开关开但 Redis 故障 → fail-closed 回退 B0006，不放行。"""
    monkeypatch.setattr(settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", True)
    fake = _ListFakeRedis(fail=True)
    rl = _reset_limiter(max_active=1, queue_max=1, fake=fake, deferred_max=10)
    # Redis 挂：幂等锁降级放行仍可占满闸与队列
    for i in range(2):
        assert await rl.acquire_complaint(f"hold-{i}") in (OUTCOME_GRANTED, OUTCOME_QUEUED)
    cid = complaint_service.create("Redis挂溢出投诉")["complaint_id"]

    async with _client() as ac:
        r = await ac.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": False})

    assert r.status_code == 200
    assert r.json()["code"] == "B0006"


# ── 3.4 / 3.5 补偿循环 ──


def _patch_loop_timing(monkeypatch) -> None:
    """压缩循环空转/退避间隔，避免单测真实 sleep 拖慢。"""
    monkeypatch.setattr("app.main._DEFERRED_IDLE_INTERVAL", 0.01)
    monkeypatch.setattr("app.main._DEFERRED_BACKOFF_BASE", 0.01)
    monkeypatch.setattr("app.main._DEFERRED_BACKOFF_MAX", 0.04)


async def _run_loop_briefly(monkeypatch, pops: list, outcomes: dict) -> _FakeLoopLimiter:
    """注入 fake 限流器，启动补偿循环处理给定 pop 序列后取消，返回 fake 供断言。"""
    fake = _FakeLoopLimiter(pops=pops, outcomes=outcomes)
    monkeypatch.setattr("app.service.rate_limiter.rate_limiter", fake)
    task = asyncio.create_task(_deferred_consumer_loop())
    await asyncio.sleep(0.08)  # 处理 pop 序列 + 空转一轮
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return fake


@pytest.mark.asyncio
async def test_loop_granted_executes_and_releases(monkeypatch):
    """granted → judge_async(run_graph=True) 执行一次，并释放并发槽 + 维5锁。"""
    _patch_loop_timing(monkeypatch)
    judge_calls: list[str] = []

    async def fake_judge(cid, run_graph=False, **_kw):
        judge_calls.append((cid, run_graph))
        return {}

    monkeypatch.setattr(complaint_service, "judge_async", fake_judge)

    out = await _run_loop_briefly(monkeypatch, ["c1", None], {"c1": OUTCOME_GRANTED})
    assert judge_calls == [("c1", True)]
    assert out.released_locks == ["c1"]
    assert out.released_slots == 1
    assert out.requeued == []


@pytest.mark.asyncio
async def test_loop_queued_no_duplicate_trigger(monkeypatch):
    """queued → 已进内存队列，不重复触发 judge、不重塞、不释放槽。"""
    _patch_loop_timing(monkeypatch)
    judge_calls: list[str] = []

    async def fake_judge(cid, run_graph=False, **_kw):  # noqa: ARG001
        judge_calls.append(cid)

    monkeypatch.setattr(complaint_service, "judge_async", fake_judge)

    out = await _run_loop_briefly(monkeypatch, ["c1", None], {"c1": OUTCOME_QUEUED})
    assert judge_calls == []  # 不重复触发
    assert out.requeued == []
    assert out.released_slots == 0


@pytest.mark.asyncio
async def test_loop_duplicate_ignored(monkeypatch):
    """duplicate → 在途/已完成幂等忽略：不执行、不重塞。"""
    _patch_loop_timing(monkeypatch)
    judge_calls: list[str] = []

    async def fake_judge(cid, run_graph=False, **_kw):  # noqa: ARG001
        judge_calls.append(cid)

    monkeypatch.setattr(complaint_service, "judge_async", fake_judge)

    out = await _run_loop_briefly(monkeypatch, ["c1", None], {"c1": OUTCOME_DUPLICATE})
    assert judge_calls == []
    assert out.requeued == []
    assert out.released_slots == 0


@pytest.mark.asyncio
async def test_loop_full_backoff_requeue(monkeypatch):
    """full → 指数退避后重塞队尾，不执行 judge（禁止无退避空转）。"""
    _patch_loop_timing(monkeypatch)
    judge_calls: list[str] = []

    async def fake_judge(cid, run_graph=False, **_kw):  # noqa: ARG001
        judge_calls.append(cid)

    monkeypatch.setattr(complaint_service, "judge_async", fake_judge)

    out = await _run_loop_briefly(monkeypatch, ["c1", None], {"c1": OUTCOME_FULL})
    assert judge_calls == []
    assert out.requeued == ["c1"]


@pytest.mark.asyncio
async def test_loop_judge_exception_warns_and_continues(monkeypatch):
    """judge 非取消异常 → warn 后循环继续：槽/锁已释放、失败单不重塞（防毒丸）。"""
    _patch_loop_timing(monkeypatch)
    judge_calls: list[str] = []

    async def flaky_judge(cid, run_graph=False, **_kw):  # noqa: ARG001
        judge_calls.append(cid)
        if cid == "bad":
            raise ValueError("boom")
        return {}

    monkeypatch.setattr(complaint_service, "judge_async", flaky_judge)

    out = await _run_loop_briefly(
        monkeypatch,
        ["bad", "ok", None],
        {"bad": OUTCOME_GRANTED, "ok": OUTCOME_GRANTED},
    )
    assert judge_calls == ["bad", "ok"]  # 异常后循环继续处理下一条
    assert out.requeued == []  # 失败单不重塞（防毒丸无限重试）
    assert out.released_locks == ["bad", "ok"]  # 槽与维5锁均已释放
    assert out.released_slots == 2


@pytest.mark.asyncio
async def test_loop_cancel_requeues_inflight_cid(monkeypatch):
    """3.5 关停不丢：judge 在途被取消 → cid 重塞回队列，槽与维5锁已释放。"""
    _patch_loop_timing(monkeypatch)
    fake = _FakeLoopLimiter(pops=["c1", None], outcomes={"c1": OUTCOME_GRANTED})
    monkeypatch.setattr("app.service.rate_limiter.rate_limiter", fake)
    entered = asyncio.Event()

    async def hanging_judge(cid, run_graph=False, **_kw):  # noqa: ARG001
        entered.set()
        await asyncio.sleep(10)  # 模拟在途研判，被关停取消打断
        return {}

    monkeypatch.setattr(complaint_service, "judge_async", hanging_judge)

    task = asyncio.create_task(_deferred_consumer_loop())
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert fake.requeued == ["c1"]  # 在途 cid 重塞，重启后队列仍有该 cid
    assert fake.released_locks == ["c1"]  # 并发槽与维5锁已释放
    assert fake.released_slots == 1


@pytest.mark.asyncio
async def test_loop_requeue_survives_second_cancel(monkeypatch):
    """3.5 加固：重塞途中遭第二次 cancel，shield 保证 LPUSH 仍落盘（不丢）。"""
    _patch_loop_timing(monkeypatch)
    fake = _FakeLoopLimiter(pops=["c1"], outcomes={"c1": OUTCOME_GRANTED})
    fake.requeue_delay = 0.2  # 重塞挂起窗口，供注入二次 cancel
    monkeypatch.setattr("app.service.rate_limiter.rate_limiter", fake)
    entered = asyncio.Event()

    async def hanging_judge(cid, run_graph=False, **_kw):  # noqa: ARG001
        entered.set()
        await asyncio.sleep(10)  # 模拟在途研判被第一次关停取消打断
        return {}

    monkeypatch.setattr(complaint_service, "judge_async", hanging_judge)

    task = asyncio.create_task(_deferred_consumer_loop())
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    task.cancel()  # 第一次 cancel：打断 judge → 进入 CancelledError 分支 → await 重塞
    await asyncio.sleep(0.05)  # 等重塞协程进入挂起
    task.cancel()  # 第二次 cancel：打断 shield 外层（shield 内 LPUSH 继续跑）
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)  # 给 shield 内重塞协程完成时间（事件循环仍调度它）
    assert fake.requeued == ["c1"]  # 二次 cancel 也不丢，cid 最终落盘
    assert fake.released_locks == ["c1"]
    assert fake.released_slots == 1


# ── 3.6 端到端冒烟：闸+队列满溢出 → 202 → 补位执行且仅一次 ──


@pytest.mark.asyncio
async def test_e2e_521st_overflow_with_production_gates(monkeypatch):
    """生产闸（20+500）灌满后，第 521 条 HTTP 接入 → 202 溢出入 Redis。"""
    monkeypatch.setattr(settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", True)
    fake = _ListFakeRedis()
    rl = _reset_limiter(fake=fake)  # 默认 max_active=20 / queue_max=500
    for i in range(20):
        assert await rl.acquire_complaint(f"hold-{i}") == OUTCOME_GRANTED
    for i in range(500):
        assert await rl.acquire_complaint(f"q-{i}") == OUTCOME_QUEUED
    cid521 = complaint_service.create("第 521 条投诉")["complaint_id"]

    async with _client() as ac:
        r = await ac.post(f"/api/v1/complaints/{cid521}/judge", json={"run_graph": False})

    assert r.status_code == 202  # 第 521+ 条返回 202（非 429/B0006）
    assert r.json()["code"] == 0
    assert fake.lists[deferred_queue_key()] == [cid521]  # 已入 Redis 延迟队列
    assert judge_lock_key(cid521) not in fake.store  # 维5锁已回滚，补位接入不会误判 duplicate


@pytest.mark.asyncio
async def test_e2e_overflow_backfills_and_executes_once(monkeypatch):
    """8 条并发（闸=2 + 队列=3）：3 条溢出 202 入 Redis，资源释放后补位执行，
    同一 cid 的 judge_async 调用次数 = 1（不丢不重）。"""
    monkeypatch.setattr(settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", True)
    fake = _ListFakeRedis()
    rl = _reset_limiter(max_active=2, queue_max=3, fake=fake, deferred_max=10)
    monkeypatch.setattr("app.main._QUEUE_CONSUME_INTERVAL", 0.02)
    monkeypatch.setattr("app.main._DEFERRED_IDLE_INTERVAL", 0.02)
    monkeypatch.setattr("app.main._DEFERRED_BACKOFF_BASE", 0.02)
    monkeypatch.setattr("app.main._DEFERRED_BACKOFF_MAX", 0.08)

    counts: dict[str, int] = {}

    async def fake_judge(cid, run_graph=False, **_kw):  # noqa: ARG001
        counts[cid] = counts.get(cid, 0) + 1
        await asyncio.sleep(0.01)
        return complaint_service.get(cid) or {}

    monkeypatch.setattr(complaint_service, "judge_async", fake_judge)

    cids = [complaint_service.create(f"压测投诉 {i}")["complaint_id"] for i in range(8)]
    async with _client() as ac:
        rs = await asyncio.gather(
            *[ac.post(f"/api/v1/complaints/{c}/judge", json={"run_graph": False}) for c in cids]
        )

    statuses = [r.status_code for r in rs]
    assert statuses.count(200) == 5  # 2 granted + 3 queued
    assert statuses.count(202) == 3  # 第 6+ 条（521+ 的等价物）溢出 202
    assert len(fake.lists[deferred_queue_key()]) == 3  # 已入 Redis 延迟队列
    for r in rs:
        if r.status_code == 202:
            assert r.json()["code"] == 0
            assert r.json()["data"]["message"] == "judge queued in deferred backlog"

    # 资源释放后由补偿循环 + 内存队列消费循环补位执行
    deferred_t = asyncio.create_task(_deferred_consumer_loop())
    consumer_t = asyncio.create_task(_queue_consumer_loop())
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len(counts) == 8 and not fake.lists.get(deferred_queue_key()):
                break
            await asyncio.sleep(0.05)
        assert len(counts) == 8  # 全部被补位执行（含溢出 3 条）
        assert all(v == 1 for v in counts.values())  # 同一 cid 仅执行一次（幂等证据）
        assert not fake.lists.get(deferred_queue_key())  # Redis 队列清空
        await asyncio.sleep(0.1)  # 等待派发任务 finally 释放槽/锁
        assert rl.inflight == 0  # 并发槽无泄漏
        for cid in cids:
            assert judge_lock_key(cid) not in fake.store  # 维5锁全部释放
    finally:
        deferred_t.cancel()
        consumer_t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await deferred_t
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_t
