"""Redis Pub/Sub adapter reference-unit tests (not a service runtime mode).

``RedisPubSubEventBus`` remains on disk for对照；factory MUST NOT load it.
These tests exercise the adapter class in isolation with a fake Redis.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from common.eventbus.envelope import EventEnvelope
from common.eventbus.redis_pubsub import RedisPubSubEventBus


class _FakePubSub:
    def __init__(self, bus: "_FakeRedis") -> None:
        self._bus = bus
        self._channels: set[str] = set()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)
        self._bus._subs.setdefault(channel, []).append(self)

    async def unsubscribe(self, channel: str) -> None:
        self._channels.discard(channel)

    async def aclose(self) -> None:
        return None

    async def listen(self):
        yield {"type": "subscribe", "data": 1}
        while True:
            item = await self._queue.get()
            yield item


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
async def test_publish_uses_event_name_channel_and_envelope():
    fake = _FakeRedis()
    bus = RedisPubSubEventBus(redis=fake)
    await bus.publish(
        "etl.task.done",
        {"case_id": "c1", "session_id": "s1", "backfill_date": "2026-09-27"},
        key="s1",
    )
    assert len(fake.published) == 1
    channel, raw = fake.published[0]
    assert channel == "etl.task.done"
    body = json.loads(raw)
    assert "_key" not in body  # key must not pollute envelope JSON
    env = EventEnvelope.model_validate(body)
    assert env.data["case_id"] == "c1"
    # Same channel naming as direct Redis QC path (event_name == channel).
    assert channel == env.event_name


@pytest.mark.asyncio
async def test_subscribe_delivers_envelope_to_handler():
    fake = _FakeRedis()
    bus = RedisPubSubEventBus(redis=fake)
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    await bus.subscribe("call.session.ended", "gid_qc_call_session_ended", handler)
    await asyncio.sleep(0.05)
    await bus.publish("call.session.ended", {"session_id": "s9"})
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0].data["session_id"] == "s9"
    await bus.aclose()


@pytest.mark.asyncio
async def test_handler_retry_then_exhaust_no_dlq():
    fake = _FakeRedis()
    bus = RedisPubSubEventBus(redis=fake)
    attempts = {"n": 0}

    async def boom(_: EventEnvelope) -> None:
        attempts["n"] += 1
        raise RuntimeError("handler fail")

    await bus.subscribe("etl.task.done", "gid_qc_etl_task_done", boom)
    await asyncio.sleep(0.05)
    await bus.publish(
        "etl.task.done",
        {"case_id": "c1", "session_id": "s1", "backfill_date": "2026-09-27"},
    )
    for _ in range(80):
        if attempts["n"] >= 3:
            break
        await asyncio.sleep(0.1)
    assert attempts["n"] == 3
    await bus.aclose()
