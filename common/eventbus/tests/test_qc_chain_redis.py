"""Local verification for tasks 4.1–4.4 over EVENT_BUS=redis (fake redis)."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from common.eventbus.envelope import EventEnvelope
from common.eventbus.factory import reset_event_bus
from common.eventbus.redis_pubsub import RedisPubSubEventBus
from common.eventbus.util import whitelist_data


class _FakePubSub:
    def __init__(self, bus: "_FakeRedis") -> None:
        self._bus = bus
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self._bus._subs.setdefault(channel, []).append(self)

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def listen(self):
        yield {"type": "subscribe", "data": 1}
        while True:
            yield await self._queue.get()


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self._subs: dict[str, list[_FakePubSub]] = {}

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self)

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        for sub in self._subs.get(channel, []):
            await sub._queue.put({"type": "message", "channel": channel, "data": message})
        return 1

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_etl_and_completed_roundtrip_via_eventbus():
    os.environ["EVENT_BUS"] = "redis"
    os.environ["EVENT_BUS_SVC"] = "dp"
    reset_event_bus()
    fake = _FakeRedis()
    bus = RedisPubSubEventBus(redis=fake)

    etl_got: list[EventEnvelope] = []
    completed_got: list[EventEnvelope] = []

    async def on_etl(env: EventEnvelope) -> None:
        etl_got.append(env)

    async def on_completed(env: EventEnvelope) -> None:
        completed_got.append(env)

    await bus.subscribe("etl.task.done", "qc", on_etl)
    await bus.subscribe("qc.check.completed", "dp", on_completed)
    await asyncio.sleep(0.05)

    etl_payload = {
        "case_id": "c1",
        "session_id": "s1",
        "backfill_date": "2026-09-27",
        "trace_id": "etl-c1",
        "ts": 1,
    }
    await bus.publish(
        "etl.task.done",
        whitelist_data(etl_payload),
        key="s1",
        trace_id="etl-c1",
    )

    completed_payload = {
        "qc_record_id": "q1",
        "session_id": "s1",
        "case_id": "c1",
        "call_id": None,
        "score": 80,
        "risk": "high",
        "grade": "PASS",
        "trace_id": "tr1",
        "ts": 2,
    }
    await bus.publish(
        "qc.check.completed",
        whitelist_data(completed_payload),
        key="s1",
        trace_id="tr1",
    )

    for _ in range(50):
        if etl_got and completed_got:
            break
        await asyncio.sleep(0.05)

    await bus.aclose()
    assert len(etl_got) == 1
    assert etl_got[0].data["session_id"] == "s1"
    assert etl_got[0].data["backfill_date"] == "2026-09-27"
    assert "ts" not in etl_got[0].data

    assert len(completed_got) == 1
    assert completed_got[0].data["qc_record_id"] == "q1"
    assert "score" not in completed_got[0].data
    assert "risk" not in completed_got[0].data
