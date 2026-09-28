"""5.2：complaint.created 订阅 — 幂等 create+judge；listen 接线。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.events import subscriber as sub_mod
from common.eventbus.envelope import EventEnvelope


def _envelope(event_id: str = "evt_test_created_01", **data) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_name="complaint.created",
        occurred_at=datetime.now(timezone.utc),
        trace_id="tr_test_01",
        data=data,
    )


@pytest.fixture(autouse=True)
def _reset_dedupe():
    sub_mod.reset_event_id_dedupe()
    yield
    sub_mod.reset_event_id_dedupe()


@pytest.mark.asyncio
async def test_handle_complaint_created_create_and_judge(monkeypatch):
    """事件入口：组装正文 → create → judge(run_graph=True)。"""
    calls: list[tuple] = []

    class _Svc:
        def get(self, cid):
            return None

        async def create_async(self, text, session_id=None, case_id=None, channel="phone"):
            calls.append(("create", case_id, text))
            return {"complaint_id": case_id, "task_status": "pending"}

        async def judge_async(self, complaint_id, run_graph=False):
            calls.append(("judge", complaint_id, run_graph))
            return {"complaint_id": complaint_id, "task_status": "pending_review"}

    async def _assemble(case_id):
        return ("差异化正文-坐席违规", "agent_violation", "sess-1")

    monkeypatch.setattr(
        "app.service.complaint_service.complaint_service", _Svc(), raising=False
    )
    monkeypatch.setattr(
        "app.service.complaint_text_builder.assemble_complaint_text_for_case",
        _assemble,
        raising=False,
    )
    # 绕过 Redis：直接进程内 claim
    monkeypatch.setattr(sub_mod, "_claim_event_id", AsyncMock(return_value=True))

    await sub_mod.handle_complaint_created(_envelope(case_id="case-001"))
    assert ("create", "case-001", "差异化正文-坐席违规") in calls
    assert ("judge", "case-001", True) in calls


@pytest.mark.asyncio
async def test_handle_complaint_created_event_id_dedupe(monkeypatch):
    """同一 event_id 重复投递：只处理一次。"""
    calls: list[str] = []

    class _Svc:
        def get(self, cid):
            return None

        async def create_async(self, *a, **k):
            calls.append("create")
            return {"complaint_id": k.get("case_id") or "c"}

        async def judge_async(self, *a, **k):
            calls.append("judge")
            return {}

    monkeypatch.setattr(
        "app.service.complaint_service.complaint_service", _Svc(), raising=False
    )
    monkeypatch.setattr(
        "app.service.complaint_text_builder.assemble_complaint_text_for_case",
        AsyncMock(return_value=("t", "compliant", "")),
        raising=False,
    )
    # 真实进程内 claim（无 Redis）
    async def _claim(eid):
        if eid in sub_mod._seen_event_ids:
            return False
        sub_mod._seen_event_ids.add(eid)
        return True

    monkeypatch.setattr(sub_mod, "_claim_event_id", _claim)

    env = _envelope(event_id="evt_dup", case_id="case-dup")
    await sub_mod.handle_complaint_created(env)
    await sub_mod.handle_complaint_created(env)
    assert calls == ["create", "judge"]


@pytest.mark.asyncio
async def test_handle_complaint_created_case_idempotent_skips_rejudge(monkeypatch):
    """不同 event_id、同 case_id 且已 pending_review：不二次跑图。"""
    calls: list[str] = []

    class _Svc:
        def get(self, cid):
            return {"complaint_id": cid, "task_status": "pending_review"}

        async def create_async(self, *a, **k):
            calls.append("create")
            return {}

        async def judge_async(self, *a, **k):
            calls.append("judge")
            return {}

    monkeypatch.setattr(
        "app.service.complaint_service.complaint_service", _Svc(), raising=False
    )
    monkeypatch.setattr(sub_mod, "_claim_event_id", AsyncMock(return_value=True))

    await sub_mod.handle_complaint_created(
        _envelope(event_id="evt_a", case_id="case-done")
    )
    assert calls == []


@pytest.mark.asyncio
async def test_listen_subscribes_complaint_created(monkeypatch):
    """listen 对 complaint.created 调 EventBus.subscribe(group=cs)。"""
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.aclose = AsyncMock()

    monkeypatch.setenv("EVENT_BUS", "rocketmq")
    monkeypatch.setenv("EVENT_BUS_SVC", "cs")
    monkeypatch.setattr(
        "common.eventbus.factory.get_event_bus", lambda: bus
    )

    subscriber = sub_mod.ComplaintEventSubscriber()

    async def _sleep_then_cancel(_delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep_then_cancel)

    with pytest.raises(asyncio.CancelledError):
        await subscriber.listen()

    bus.subscribe.assert_awaited()
    args = bus.subscribe.await_args
    assert args.args[0] == "complaint.created"
    assert args.args[1] == "cs"
    bus.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_start_eventbus_subscriber_failure_does_not_raise(monkeypatch):
    """注册失败返回 None，不抛给 lifespan。"""
    monkeypatch.delenv("CS_SKIP_EVENTBUS", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(sub_mod, "skip_eventbus_subscriber", lambda: False)
    monkeypatch.setattr(
        sub_mod,
        "ComplaintEventSubscriber",
        lambda: (_ for _ in ()).throw(RuntimeError("init fail")),
    )
    task = await sub_mod.start_eventbus_subscriber()
    assert task is None


@pytest.mark.asyncio
async def test_start_skipped_under_pytest(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_foo")
    task = await sub_mod.start_eventbus_subscriber()
    assert task is None
