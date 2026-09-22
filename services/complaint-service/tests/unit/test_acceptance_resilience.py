"""G8 全量验收（tasks 8.1-8.9，HTTP 层 + 消费循环 glue）。

本文件直接验收：
- 8.1 mock graph 超时 → HTTP 200 + pending_review + exception_info(timeout) + A5010
  （节点级 [规则兜底] 草稿证据：test_node_degrade.py）
- 8.3 50 并发 → 20 granted 在跑 + 30 queued（响应 message 可见排队状态）
- 8.4 队列满第 501 条 → 响应体 B0006（非 500、不转人审；幂等锁回滚可重试）
- 消费循环 glue：ready → 派发执行（槽/锁释放）；expired → A5010 + 锁释放

其余验收点由既有专项文件提供证据：
- 8.2 fallback 链：test_llm_provider_fallback.py
- 8.5/8.6 两级 deadline：test_workflow_deadlines.py
- 8.7 MCP 熔断按 server 隔离：test_mcp_client.py
- 8.8 hooks 事件经 publisher：test_hooks_publisher.py
- 8.9 v5 验收全绿：全量回归（2 个 test_batch10 预存在失败除外）
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.events.publisher import publisher
from app.main import _consume_queue_once, app
from app.service.complaint_service import complaint_service
from app.service.rate_limiter import rate_limiter
from app.utils.redis_keys import judge_lock_key, queue_start_key
from common.resilience.concurrency import Semaphore
from common.resilience.queue import BoundedQueue
from tests.conftest import _GlobalFakeRedis


@pytest.fixture(autouse=True)
def _isolate_limiter():
    publisher.clear()
    yield
    _reset_limiter()
    publisher.clear()
    # 本文件会批量创建 70+ 内存工单，不清会挤出后续 list 测试的第 1 页分页
    complaint_service._cache.clear()


def _reset_limiter(max_active: int = 20, queue_max: int = 500, fake=None):
    """重建限流器单例内部状态（并发槽/队列/成员表），测试隔离用。"""
    from common.resilience._redis import configure_redis

    if fake is not None:
        configure_redis(fake)
    rate_limiter._max_active = max_active
    rate_limiter._queue_max = queue_max
    rate_limiter._sem = Semaphore(max_active)
    rate_limiter._queue = BoundedQueue(queue_max)
    rate_limiter._queued.clear()
    return rate_limiter


class _TimeoutGraph:
    """astream 立即抛 TimeoutError 的 stub 图（8.1 模拟 LLM 全链超时）。"""

    def astream(self, _state, _config, **_kwargs):
        return self._stream()

    async def _fail(self):
        raise TimeoutError("llm chain timeout")

    async def _stream(self):
        yield await self._fail()  # 保持 async generator 语义，首个 chunk 即抛超时

    async def aget_state(self, _config):
        return None


class _EmptyGraph:
    """astream 立即正常结束的 stub 图（消费循环 glue 用）。"""

    def astream(self, _state, _config, **_kwargs):
        return self._stream()

    async def _stream(self):
        yield {}

    async def aget_state(self, _config):
        return None


class _SlowGraph:
    """astream 睡眠 0.2s 的 stub 图（BUG-1：在途任务引用持有断言用）。"""

    def astream(self, _state, _config, **_kwargs):
        return self._stream()

    async def _stream(self):
        await asyncio.sleep(0.2)
        yield {}

    async def aget_state(self, _config):
        return None


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── 8.1 LLM 超时 → pending_review + HTTP 200 ──


@pytest.mark.asyncio
async def test_81_llm_timeout_pending_review_http200(monkeypatch):
    fake = _GlobalFakeRedis()
    _reset_limiter(fake=fake)
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph", lambda **_k: _TimeoutGraph()
    )
    cid = complaint_service.create("LLM 全链超时投诉")["complaint_id"]

    async with _client() as ac:
        r = await ac.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": True})

    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["task_status"] == "pending_review"
    rec = complaint_service.get(cid)
    assert rec["exception_info"]["error_type"] == "timeout"
    assert any(
        e.payload.get("code") == "A5010"
        for e in publisher.list_events("complaint.degraded")
    )
    # exec_start 执行结束即删
    assert f"csmp:complaint:exec_start:{cid}" not in fake.store


# ── 8.3 50 并发 → 20 在跑 + 30 在队列 ──


@pytest.mark.asyncio
async def test_83_50_concurrent_20_running_30_queued(monkeypatch):
    fake = _GlobalFakeRedis()
    rl = _reset_limiter(fake=fake)
    cids = [complaint_service.create(f"并发投诉 {i}")["complaint_id"] for i in range(50)]

    async def slow_judge(cid, **_kwargs):
        await asyncio.sleep(0.15)
        return complaint_service._judge_stub(cid)

    monkeypatch.setattr(complaint_service, "judge_async", slow_judge)

    async def _post(ac, cid):
        r = await ac.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": False})
        return r.json()

    async with _client() as ac:
        bodies = await asyncio.gather(*[_post(ac, cid) for cid in cids])

    accepted = [b for b in bodies if b["data"]["message"].startswith("judge accepted")]
    queued = [b for b in bodies if b["data"]["message"].startswith("judge queued")]
    assert len(accepted) == 20
    assert len(queued) == 30
    assert rl.queue_depth == 30
    # 排队工单已写 queue_start（10min TTL），页面/接口可展示排队状态
    assert any(k.startswith("csmp:complaint:queue_start:") for k in fake.store)


# ── 8.4 队列满第 501 条 → B0006 ──


@pytest.mark.asyncio
async def test_84_queue_full_501_returns_b0006(monkeypatch):
    fake = _GlobalFakeRedis()
    rl = _reset_limiter(fake=fake)
    cids = [complaint_service.create(f"满载投诉 {i}")["complaint_id"] for i in range(20)]

    async def slow_judge(cid, **_kwargs):
        await asyncio.sleep(0.2)
        return complaint_service._judge_stub(cid)

    monkeypatch.setattr(complaint_service, "judge_async", slow_judge)

    async def _post(ac, cid):
        r = await ac.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": False})
        return r.json()

    async with _client() as ac:
        tasks = [asyncio.create_task(_post(ac, cid)) for cid in cids]
        while rl.inflight < 20:
            await asyncio.sleep(0.01)
        # 直接灌满 500 排队位（服务级，等价于并发到达）
        for i in range(500):
            assert await rl.acquire_complaint(f"bulk-{i}") == "queued"
        # 第 501 条走 HTTP 接入
        cid501 = complaint_service.create("第 501 条投诉")["complaint_id"]
        r = await ac.post(f"/api/v1/complaints/{cid501}/judge", json={"run_graph": False})
        # 系统仍满载（20 在跑 + 500 排队）：FULL 回滚幂等锁后同 id 再接入
        # 重新进入正常判定得 full，而非被 30min 锁误判 duplicate
        assert await rl.acquire_complaint(cid501) == "full"
        await asyncio.gather(*tasks)

    body = r.json()
    assert r.status_code == 200  # 不是 500
    assert body["code"] == "B0006"
    assert body["data"] is None  # 不转人审（区别于 pending_review 响应）
    # FULL 路径回滚幂等锁：数据中台可退避重试
    assert judge_lock_key(cid501) not in fake.store


# ── 消费循环 glue：ready 派发 / expired 弹出 ──


@pytest.mark.asyncio
async def test_consumer_ready_dispatches_judge(monkeypatch):
    fake = _GlobalFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=10, fake=fake)
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph", lambda **_k: _EmptyGraph()
    )
    cid = complaint_service.create("排队后消费执行")["complaint_id"]
    assert await rl.acquire_complaint("hold") == "granted"
    assert await rl.acquire_complaint(cid) == "queued"
    rl.release_complaint()

    await _consume_queue_once()
    await asyncio.sleep(0.05)  # 等待派发 task 完成

    rec = complaint_service.get(cid)
    assert rec["task_status"] == "pending_review"
    assert rl.inflight == 0  # 执行结束释放并发槽
    assert judge_lock_key(cid) not in fake.store  # 维5幂等锁释放


@pytest.mark.asyncio
async def test_consumer_expired_marks_a5010():
    fake = _GlobalFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=10, fake=fake)
    cid = complaint_service.create("排队超时消费")["complaint_id"]
    assert await rl.acquire_complaint("hold") == "granted"
    assert await rl.acquire_complaint(cid) == "queued"
    # 模拟排队超 10min：queue_start TTL 到期
    del fake.store[queue_start_key(cid)]
    rl.release_complaint()

    await _consume_queue_once()

    rec = complaint_service.get(cid)
    assert rec["task_status"] == "pending_review"
    assert rec["exception_info"]["error_type"] == "queue_timeout"
    a5010 = [
        e.payload
        for e in publisher.list_events("complaint.degraded")
        if e.payload.get("code") == "A5010"
    ]
    assert len(a5010) == 1
    # GAP-2：消费循环路径透传真实 trace_id 与来源 node
    assert a5010[0]["trace_id"] == f"trace-{cid}"
    assert a5010[0]["node"] == "queue_consumer"
    assert judge_lock_key(cid) not in fake.store  # 幂等锁释放，可重判


# ── BUG-1 回归：消费派发任务须强引用持有，结束自动移除 ──
@pytest.mark.asyncio
async def test_consumer_spawned_task_is_referenced_until_done(monkeypatch):
    """create_task 返回值若被 GC，研判会中途消失且槽/锁不释放。"""
    from app.main import _consume_queue_once, _running_queued

    fake = _GlobalFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=10, fake=fake)
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph", lambda **_k: _SlowGraph()
    )
    cid = complaint_service.create("在途任务引用持有")["complaint_id"]
    assert await rl.acquire_complaint("hold") == "granted"
    assert await rl.acquire_complaint(cid) == "queued"
    rl.release_complaint()

    await _consume_queue_once()
    # 任务在途：被集合强引用（防 GC）
    assert len(_running_queued) == 1
    await asyncio.sleep(0.3)  # 等研判结束 → done_callback 自动移除
    assert len(_running_queued) == 0
    assert rl.inflight == 0
    assert judge_lock_key(cid) not in fake.store


# ── BUG-2 回归：requeue 时空位被抢满，工单转 expired 而非静默丢失 ──

@pytest.mark.asyncio
async def test_consume_requeue_when_refilled_full_goes_expired(monkeypatch):
    """pop 出的工单遇无槽回队尾时，若唯一空位已被新请求填满：
    必须进 expired（→ A5010 转人审 + 锁释放），不得从队列/成员表双向消失。"""
    fake = _GlobalFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=2, fake=fake)
    cid = complaint_service.create("回队失败投诉")["complaint_id"]
    assert await rl.acquire_complaint("hold") == "granted"
    assert await rl.acquire_complaint(cid) == "queued"
    assert await rl.acquire_complaint("b") == "queued"  # 队列=[cid, b] 满

    async def _alive_then_intruder_fills_gap(item):  # noqa: ARG001
        # pop(cid) 后队列剩 [b]、空 1 位：模拟新请求在 await 间隙抢占填满
        assert await rl._queue.push("intruder") is True
        return True

    monkeypatch.setattr(rl, "_queue_start_alive", _alive_then_intruder_fills_gap)

    try:
        ready, expired = await rl.consume_pending(max_n=1)
        assert ready == []
        assert expired == [cid]
        assert cid not in rl._queued  # 成员表同步移除，不再永久挂"排队中"

        # 消费循环对 expired 的落地路径：A5010 + pending_review + 锁释放
        rec = await complaint_service.fail_queue_timeout(cid)
        assert rec["task_status"] == "pending_review"
        assert judge_lock_key(cid) not in fake.store
    finally:
        rl.release_complaint()


# ── GAP-1：用户维接入限流 → B0006，不占并发槽/幂等锁 ──


@pytest.mark.asyncio
async def test_user_dimension_rate_limit_returns_b0006(monkeypatch):
    fake = _GlobalFakeRedis()
    rl = _reset_limiter(fake=fake)
    cid = complaint_service.create("用户维限流投诉")["complaint_id"]

    async def _deny(user_key, **_kwargs):  # noqa: ARG001
        return False

    monkeypatch.setattr(rl, "acquire_user", _deny)

    async with _client() as ac:
        r = await ac.post(
            f"/api/v1/complaints/{cid}/judge",
            json={"run_graph": False},
            headers={"X-User-Id": "spammer-1"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "B0006"
    # 用户维拒绝发生在 acquire_complaint 之前：未占槽、未占幂等锁
    assert rl.inflight == 0
    assert judge_lock_key(cid) not in fake.store


# ── P3-4 回归：shutdown 取消在途研判 → best-effort pending_review + 槽/锁释放 ──


class _HangGraph:
    """astream 永久挂起，直至任务被外部 cancel（模拟关停瞬间的在途研判）。"""

    def astream(self, _state, _config, **_kwargs):
        return self._stream()

    async def _stream(self):
        await asyncio.Event().wait()
        yield {}  # pragma: no cover

    async def aget_state(self, _config):
        from types import SimpleNamespace

        return SimpleNamespace(values={})


@pytest.mark.asyncio
async def test_shutdown_cancel_marks_pending_review_and_releases(monkeypatch):
    """关停取消在途排队研判：session 不滞留流水线态，槽/锁仍释放，任务正常终态。"""
    from app.main import _consume_queue_once, _running_queued

    fake = _GlobalFakeRedis()
    rl = _reset_limiter(max_active=1, queue_max=10, fake=fake)
    monkeypatch.setattr("app.graph.builder.build_complaint_graph", lambda **_k: _HangGraph())
    cid = complaint_service.create("关停取消在途工单")["complaint_id"]
    assert await rl.acquire_complaint("hold") == "granted"
    assert await rl.acquire_complaint(cid) == "queued"
    rl.release_complaint()

    await _consume_queue_once()
    assert len(_running_queued) == 1
    await asyncio.sleep(0)  # 让在途任务进入挂起点

    task = next(iter(_running_queued))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rec = complaint_service.get(cid)
    assert rec["task_status"] == "pending_review"
    assert rec["exception_info"]["error_type"] == "shutdown_cancelled"
    assert rec["exception_info"]["node"] == "queue_consumer"
    # finally 语义不被取消破坏：槽释放、维5锁释放，可重判
    assert rl.inflight == 0
    assert judge_lock_key(cid) not in fake.store
    assert len(_running_queued) == 0


@pytest.mark.asyncio
async def test_fail_shutdown_guards_terminal_and_existing_review():
    """fail_shutdown：终态工单不回退；已 pending_review 的既有 exception_info 不被覆盖。"""
    from app.models.state import TaskStatus
    from app.service.session_service import session_service

    # 1) 流水线内 → 兜底转人审
    cid1 = complaint_service.create("关停兜底-在途")["complaint_id"]
    assert await complaint_service.fail_shutdown(cid1) is True
    rec1 = complaint_service.get(cid1)
    assert rec1["task_status"] == "pending_review"
    assert rec1["exception_info"]["error_type"] == "shutdown_cancelled"

    # 2) 已 pending_review 且已有异常信息 → 幂等成功但不覆盖
    cid2 = complaint_service.create("关停兜底-已人审")["complaint_id"]
    sid2 = complaint_service.get(cid2)["session_id"]
    session2 = session_service.get_session(sid2)
    session2["exception_info"] = {
        "error_type": "queue_timeout",
        "message": "x",
        "node": "queue_consumer",
    }
    assert session_service.transition(sid2, TaskStatus.pending_review) is True
    assert await complaint_service.fail_shutdown(cid2) is True
    assert session2["exception_info"]["error_type"] == "queue_timeout"

    # 3) 已终态（resolved）→ 拒绝回退，返回 False
    cid3 = complaint_service.create("关停兜底-已终态")["complaint_id"]
    sid3 = complaint_service.get(cid3)["session_id"]
    for nxt in (
        TaskStatus.evidence_gathering,
        TaskStatus.judging,
        TaskStatus.draft_generated,
        TaskStatus.pending_review,
        TaskStatus.pending_callback,
        TaskStatus.resolved,
    ):
        assert session_service.transition(sid3, nxt) is True
    assert await complaint_service.fail_shutdown(cid3) is False
    assert complaint_service.get(cid3)["task_status"] == "resolved"
