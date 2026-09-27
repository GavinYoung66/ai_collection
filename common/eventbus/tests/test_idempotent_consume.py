"""Spec: at-least-once delivery — subscribers SHALL be idempotent via ``event_id``."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from common.eventbus.envelope import EventEnvelope
from common.eventbus.factory import reset_event_bus
from common.eventbus.redis_pubsub import RedisPubSubEventBus


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
        self._subs: dict[str, list[_FakePubSub]] = {}

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self)

    async def publish(self, channel: str, message: str) -> int:
        for sub in self._subs.get(channel, []):
            await sub._queue.put({"type": "message", "channel": channel, "data": message})
        return 1

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_duplicate_event_id_processed_once_by_idempotent_handler():
    """Same ``event_id`` redelivered → handler processes once (spec 幂等消费)."""
    os.environ["EVENT_BUS"] = "redis"
    os.environ["EVENT_BUS_SVC"] = "qc"
    reset_event_bus()

    fake = _FakeRedis()
    bus = RedisPubSubEventBus(redis=fake)
    seen: set[str] = set()
    outcomes: list[str] = []

    async def idempotent_handler(envelope: EventEnvelope) -> None:
        if envelope.event_id in seen:
            outcomes.append("skip")
            return
        seen.add(envelope.event_id)
        outcomes.append("process")

    await bus.subscribe("etl.task.done", "qc", idempotent_handler)
    await asyncio.sleep(0.05)

    await bus.publish(
        "etl.task.done",
        {"session_id": "s-dup", "case_id": "c-dup", "backfill_date": "2026-09-27"},
        key="s-dup",
        event_id="evt_dup_fixed",
        trace_id="tr_dup",
    )
    # Simulate at-least-once redelivery of the identical event_id.
    await bus.publish(
        "etl.task.done",
        {"session_id": "s-dup", "case_id": "c-dup", "backfill_date": "2026-09-27"},
        key="s-dup",
        event_id="evt_dup_fixed",
        trace_id="tr_dup",
    )

    for _ in range(50):
        if len(outcomes) >= 2:
            break
        await asyncio.sleep(0.05)

    await bus.aclose()
    assert outcomes == ["process", "skip"]
    assert seen == {"evt_dup_fixed"}


@pytest.mark.asyncio
async def test_envelope_exposes_event_id_for_dedup():
    """EventBus SHALL expose ``event_id`` so subscribers can dedupe."""
    from common.eventbus.envelope import build_envelope

    env = build_envelope(
        "qc.check.completed",
        {"qc_record_id": "q1", "session_id": "s1", "case_id": "c1"},
        event_id="evt_idem_1",
        trace_id="tr_idem_1",
    )
    assert env.event_id == "evt_idem_1"
    assert env.trace_id == "tr_idem_1"
