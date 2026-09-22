"""两级工单 deadline 单测（spec: session-service Requirement 2，tasks 5.4）。

覆盖：
1. 排队超 10min（queue_start 失效）→ judge_async 弹出不进图，pending_review + A5010
2. graph 执行超 30min（wait_for 缩短为 0.05s）→ pending_review + A5010 + exception_info
3. 两级 Redis key 独立：queue_start 在获槽时删、exec_start 在执行结束删，互不删对方
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.events.publisher import publisher
from app.models.state import TaskStatus
from app.service.complaint_service import complaint_service
from app.service.rate_limiter import rate_limiter
from app.service.session_service import session_service
from app.utils.redis_keys import exec_start_key, queue_start_key
from common.resilience._redis import configure_redis, get_redis, reset_redis


class FakeRedis:
    """记录 set(ex)/delete 顺序的内存 Redis。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, int | None]] = []
        self.delete_calls: list[str] = []

    async def set(self, key, value, nx: bool = False, ex: int | None = None):  # noqa: ARG002
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        self.set_calls.append((key, ex))
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        n = 0
        for k in keys:
            self.delete_calls.append(k)
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    async def ping(self):
        return True

    async def incr(self, key):
        return 1

    async def expire(self, key, window):  # noqa: ARG002
        return None


class _EmptyGraph:
    """astream 立即正常结束的 stub 图。"""

    def astream(self, _state, _config, stream_mode="updates"):  # noqa: ARG002
        return self._stream()

    async def _stream(self):
        yield {}

    async def aget_state(self, _config):
        return SimpleNamespace(values={})


class _SlowGraph:
    """astream 睡眠超过执行 deadline 的 stub 图。"""

    def astream(self, _state, _config, stream_mode="updates"):  # noqa: ARG002
        return self._stream()

    async def _stream(self):
        await asyncio.sleep(0.3)
        yield {}  # pragma: no cover

    async def aget_state(self, _config):
        return SimpleNamespace(values={})


@pytest.fixture(autouse=True)
def _isolate():
    fake = FakeRedis()
    configure_redis(fake)
    publisher.clear()
    yield
    reset_redis()
    publisher.clear()


async def _fill_slots_and_enqueue(cid: str) -> None:
    """占满并发槽后让 cid 入队；返回前 20 个槽位由用例在 finally 中释放。"""
    for i in range(rate_limiter.max_active):
        assert await rate_limiter.acquire_complaint(f"hold-{cid}-{i}") == "granted"
    assert await rate_limiter.acquire_complaint(cid) == "queued"


def _release_all_slots() -> None:
    for _ in range(rate_limiter.max_active):
        rate_limiter.release_complaint()


def _status(sid: str) -> str:
    s = session_service.get_session(sid)
    v = s["task_status"]
    return v.value if hasattr(v, "value") else str(v)


def _a5010_events() -> list[dict]:
    return [
        e.payload
        for e in publisher.list_events("complaint.degraded")
        if e.payload.get("code") == "A5010"
    ]


@pytest.mark.asyncio
async def test_queue_timeout_pending_review_a5010():
    """排队 >10min（queue_start 已过期）→ 弹出不进图，pending_review + A5010。"""
    fake: FakeRedis = get_redis()
    cid = complaint_service.create(complaint_text="排队超时投诉")["complaint_id"]
    await _fill_slots_and_enqueue(cid)
    try:
        # 模拟 queue_start TTL 到期
        del fake.store[queue_start_key(cid)]

        rec = await complaint_service.judge_async(cid, run_graph=True)

        sid = rec["session_id"]
        assert _status(sid) == TaskStatus.pending_review.value
        assert rec["exception_info"]["error_type"] == "queue_timeout"
        assert (
            session_service.get_session(sid)["exception_info"]["error_type"]
            == "queue_timeout"
        )
        assert len(_a5010_events()) == 1
        event = _a5010_events()[0]
        assert event["trace_id"] == f"trace-{cid}"  # 真实链路 trace，非合成 degrade-A5010
        assert event["node"] == "judge_async"
        # 未进图：exec_start 从未写过
        assert exec_start_key(cid) not in fake.store
    finally:
        _release_all_slots()


@pytest.mark.asyncio
async def test_exec_timeout_pending_review_a5010(monkeypatch):
    """执行 >30min（wait_for 缩短）→ pending_review + A5010 + exception_info(timeout)。"""
    fake: FakeRedis = get_redis()
    monkeypatch.setattr(rate_limiter, "_exec_timeout", 0.05)
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph", lambda **_k: _SlowGraph()
    )
    cid = complaint_service.create(complaint_text="执行超时投诉")["complaint_id"]

    rec = await complaint_service.judge_async(cid, run_graph=True)

    sid = rec["session_id"]
    assert _status(sid) == TaskStatus.pending_review.value
    assert rec["exception_info"]["error_type"] == "timeout"
    assert rec["exception_info"]["node"] == "graph"
    assert len(_a5010_events()) == 1
    event = _a5010_events()[0]
    assert event["trace_id"] == f"trace-{cid}"
    assert event["node"] == "graph"
    # exec_start 执行结束即删
    assert exec_start_key(cid) not in fake.store
    assert exec_start_key(cid) in fake.delete_calls


@pytest.mark.asyncio
async def test_two_level_keys_independent(monkeypatch):
    """排队工单获槽：先删 queue_start，执行结束删 exec_start，两级 TTL/删除互不干扰。"""
    fake: FakeRedis = get_redis()
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph", lambda **_k: _EmptyGraph()
    )
    cid = complaint_service.create(complaint_text="两级 key 独立性投诉")["complaint_id"]
    await _fill_slots_and_enqueue(cid)
    try:
        # queue_start 以 600s TTL 写入
        assert (queue_start_key(cid), 600) in fake.set_calls

        rec = await complaint_service.judge_async(cid, run_graph=True)

        assert _status(rec["session_id"]) == TaskStatus.pending_review.value
        # queue_start 在进图前（获槽）删除；exec_start 先以 1800s 写后删
        q_idx = fake.delete_calls.index(queue_start_key(cid))
        e_idx = fake.delete_calls.index(exec_start_key(cid))
        assert q_idx < e_idx
        assert (exec_start_key(cid), 1800) in fake.set_calls
        assert queue_start_key(cid) not in fake.store
        assert exec_start_key(cid) not in fake.store
    finally:
        _release_all_slots()
