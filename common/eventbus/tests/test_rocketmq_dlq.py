"""Task 5.1: RocketMQ exhaust → DLQ produce + dlq hook."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.eventbus.envelope import EventEnvelope, build_envelope
from common.eventbus.rocketmq import RocketMQEventBus


@pytest.mark.asyncio
async def test_handler_exhaust_sends_dlq_and_hook():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081", max_retry_times=1)
    hooks: list[tuple[str, dict[str, Any]]] = []
    dlq_calls: list[tuple] = []

    async def capture(name: str, payload: dict[str, Any]) -> None:
        hooks.append((name, payload))

    async def boom(_: EventEnvelope) -> None:
        raise RuntimeError("always fail")

    envelope = build_envelope(
        "etl.task.done",
        {"session_id": "s1", "case_id": "c1", "backfill_date": "2026-09-27"},
        event_id="evt_dlq_1",
        trace_id="tr_dlq_1",
    )
    msg = MagicMock()
    msg.body = envelope.model_dump_json().encode("utf-8")
    consumer = MagicMock()
    loop = asyncio.get_running_loop()

    def fake_publish_sync(event_name, body, key, topic):
        dlq_calls.append((event_name, body, key, topic))
        return "mid-dlq"

    with (
        patch("common.eventbus.observability.on_event", new=AsyncMock(side_effect=capture)),
        patch.object(bus, "_publish_sync", side_effect=fake_publish_sync),
        patch.object(bus, "_executor", None),
    ):
        # run_in_executor with None executor uses default — patch _handle path directly
        bus._executor = MagicMock()

        async def run_in_executor(executor, fn, *args):
            if fn is consumer.ack:
                return None
            if fn is bus._publish_sync:
                return bus._publish_sync(*args)
            return fn(*args) if callable(fn) else None

        with patch.object(loop, "run_in_executor", side_effect=run_in_executor):
            await bus._handle_message(
                msg,
                "etl.task.done",
                "gid_qc_etl_task_done",
                boom,
                consumer,
                loop,
            )

    assert any(t.startswith("%DLQ%") for *_, t in dlq_calls)
    names = [n for n, _ in hooks]
    assert "csmp.eventbus.retry" in names
    assert "csmp.eventbus.dlq" in names
    dlq_payload = next(p for n, p in hooks if n == "csmp.eventbus.dlq")
    assert dlq_payload["event_id"] == "evt_dlq_1"
    assert dlq_payload["consumer_group"] == "gid_qc_etl_task_done"
    assert "last_error" in dlq_payload


@pytest.mark.asyncio
async def test_consume_reject_log_includes_event_id_and_rejected_field():
    """Illegal wire body → eventbus_consume_reject carries identity + rejected_field."""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081", max_retry_times=0)
    illegal = {
        "event_id": "evt_bad_wire",
        "event_name": "etl.task.done",
        "occurred_at": "2026-09-27T00:00:00+00:00",
        "trace_id": "tr_bad_wire",
        "data": {"session_id": "s1", "score": 1},
    }
    msg = MagicMock()
    msg.body = __import__("json").dumps(illegal).encode("utf-8")
    consumer = MagicMock()
    loop = asyncio.get_running_loop()
    seen: dict[str, Any] = {}

    def warning_spy(event: str, **kwargs: Any) -> None:
        if event == "eventbus_consume_reject":
            seen.update(kwargs)

    async def never(_: EventEnvelope) -> None:
        raise AssertionError("handler must not run")

    with (
        patch("common.eventbus.rocketmq.logger.warning", side_effect=warning_spy),
        patch.object(bus, "_executor", MagicMock()),
    ):

        async def run_in_executor(executor, fn, *args):
            if fn is consumer.ack:
                return None
            return fn(*args) if callable(fn) else None

        with patch.object(loop, "run_in_executor", side_effect=run_in_executor):
            await bus._handle_message(
                msg,
                "etl.task.done",
                "gid_qc_etl_task_done",
                never,
                consumer,
                loop,
            )

    assert seen.get("event_id") == "evt_bad_wire"
    assert seen.get("trace_id") == "tr_bad_wire"
    assert seen.get("rejected_field") == "score"
    assert "score" in (seen.get("rejected_fields") or [])
