"""5.6：DP publish_complaint_created 信封 + 订阅 result.ready 确认日志。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events import publisher as pub_mod
from app.events import subscriber as sub_mod
from common.eventbus.envelope import EventEnvelope
from common.eventbus.util import whitelist_data


@pytest.mark.asyncio
async def test_publish_complaint_created_whitelist_envelope(monkeypatch):
    bus = MagicMock()
    bus.publish = AsyncMock()
    monkeypatch.setenv("EVENT_BUS", "rocketmq")
    monkeypatch.setenv("EVENT_BUS_SVC", "dp")
    monkeypatch.setattr("common.eventbus.factory.get_event_bus", lambda: bus)

    ok = await pub_mod.publish_complaint_created(
        case_id="11111111-2222-4333-8444-555555555501",
        event_id="evt_created_ut_01",
        trace_id="tr_created_ut",
    )
    assert ok is True
    bus.publish.assert_awaited_once()
    args, kwargs = bus.publish.await_args
    assert args[0] == "complaint.created"
    data = args[1]
    assert data == whitelist_data({"case_id": "11111111-2222-4333-8444-555555555501"})
    assert "complaint_text" not in data
    assert kwargs.get("event_id") == "evt_created_ut_01"
    assert kwargs.get("key") == "11111111-2222-4333-8444-555555555501"


@pytest.mark.asyncio
async def test_handle_complaint_result_ready_confirms(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        out = await sub_mod.handle_complaint_result_ready(
            {
                "case_id": "case-rr-1",
                "complaint_no": "case-rr-1",
                "trace_id": "tr-rr",
            }
        )
    assert out["status"] == "ok"
    assert out["confirmed"] is True
    assert any("complaint.result.ready" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_listen_subscribes_result_ready(monkeypatch):
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.aclose = AsyncMock()
    monkeypatch.setenv("EVENT_BUS", "rocketmq")
    monkeypatch.setenv("EVENT_BUS_SVC", "dp")
    monkeypatch.setattr("common.eventbus.factory.get_event_bus", lambda: bus)

    sub = sub_mod.QcCheckSubscriber()

    async def _cancel(_delay):
        raise asyncio.CancelledError()

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _cancel)

    with pytest.raises(asyncio.CancelledError):
        await sub.listen()

    channels = [c.args[0] for c in bus.subscribe.await_args_list]
    assert "qc.check.completed" in channels
    assert "complaint.result.ready" in channels
    groups = [c.args[1] for c in bus.subscribe.await_args_list]
    assert groups.count("dp") >= 2


@pytest.mark.asyncio
async def test_envelope_result_ready_dispatches():
    seen: list = []

    async def _handler(payload):
        seen.append(payload)
        return {"status": "ok"}

    sub = sub_mod.QcCheckSubscriber(result_ready_handler=_handler)
    env = EventEnvelope(
        event_id="evt_rr",
        event_name="complaint.result.ready",
        occurred_at=datetime.now(timezone.utc),
        trace_id="tr",
        data={"case_id": "c1"},
    )
    await sub.handle_envelope(env)
    assert seen == [{"case_id": "c1", "trace_id": "tr"}]
