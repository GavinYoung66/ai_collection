"""Task 6.1 / 5.1: eventbus hooks + redis retry exhaust."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

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
async def test_publish_and_consume_emit_hooks():
    fake = _FakeRedis()
    bus = RedisPubSubEventBus(redis=fake)
    hooks: list[tuple[str, dict[str, Any]]] = []

    async def capture(name: str, payload: dict[str, Any]) -> None:
        hooks.append((name, payload))

    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    with patch("common.eventbus.observability.on_event", new=AsyncMock(side_effect=capture)):
        await bus.subscribe("etl.task.done", "gid_qc_etl_task_done", handler)
        await asyncio.sleep(0.05)
        await bus.publish(
            "etl.task.done",
            {"case_id": "c1", "session_id": "s1", "backfill_date": "2026-09-27"},
            trace_id="tr_hook_1",
            event_id="evt_hook_1",
        )
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)

    await bus.aclose()
    assert received
    names = [n for n, _ in hooks]
    assert "csmp.eventbus.publish" in names
    assert "csmp.eventbus.consume" in names
    pub = next(p for n, p in hooks if n == "csmp.eventbus.publish")
    assert pub["event_id"] == "evt_hook_1"
    assert pub["event_name"] == "etl.task.done"
    assert pub["trace_id"] == "tr_hook_1"
    assert "svc" in pub


@pytest.mark.asyncio
async def test_redis_retry_emits_retry_hook_no_dlq():
    fake = _FakeRedis()
    bus = RedisPubSubEventBus(redis=fake)
    hooks: list[str] = []
    attempts = {"n": 0}

    async def capture(name: str, payload: dict[str, Any]) -> None:
        hooks.append(name)

    async def boom(_: EventEnvelope) -> None:
        attempts["n"] += 1
        raise RuntimeError("boom")

    with patch("common.eventbus.observability.on_event", new=AsyncMock(side_effect=capture)):
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

    await bus.aclose()
    assert attempts["n"] == 3
    assert hooks.count("csmp.eventbus.retry") == 3
    assert "csmp.eventbus.dlq" not in hooks
