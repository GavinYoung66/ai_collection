"""RocketMQ adapter unit tests (task 2.4, go path)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from common.eventbus.base import PHYSICAL_TOPIC, consumer_group_name
from common.eventbus.rocketmq import MAX_RETRY_TIMES, RocketMQEventBus


def test_max_retry_times_is_three():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    assert bus.max_retry_times == 3
    assert MAX_RETRY_TIMES == 3


def test_consumer_group_from_svc_short_code():
    assert consumer_group_name("qc", "call.session.ended") == "gid_qc_call_session_ended"


@pytest.mark.asyncio
async def test_publish_sets_tag_key_and_topic():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    captured: dict[str, Any] = {}

    class _Receipt:
        message_id = "mid-1"

    class _FakeProducer:
        def startup(self):
            return None

        def send(self, message):
            captured["topic"] = message.topic
            captured["tag"] = message.tag
            captured["keys"] = getattr(message, "keys", None)
            captured["body"] = message.body
            return _Receipt()

        def shutdown(self):
            return None

    with patch.object(bus, "_ensure_producer", return_value=_FakeProducer()):
        await bus.publish(
            "etl.task.done",
            {"case_id": "c1", "session_id": "s1", "backfill_date": "2026-09-27"},
            key="s1",
        )

    assert captured["topic"] == PHYSICAL_TOPIC
    assert captured["tag"] == "etl.task.done"
    keys = captured["keys"]
    assert keys == "s1" or keys == {"s1"} or (isinstance(keys, (list, set, tuple)) and "s1" in keys)
    assert b"etl.task.done" in captured["body"]
    await bus.aclose()


@pytest.mark.asyncio
async def test_subscribe_uses_gid_and_filter_expression():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    started: dict[str, Any] = {}

    class _FakeConsumer:
        def receive(self, *_a, **_k):
            return []

        def shutdown(self):
            return None

        def ack(self, *_a, **_k):
            return None

    def _start(event_name, consumer_group):
        started["event_name"] = event_name
        started["consumer_group"] = consumer_group
        return _FakeConsumer()

    with patch.object(bus, "_start_consumer", side_effect=_start):
        await bus.subscribe("call.session.ended", "qc", MagicMock())
        # give loop one tick then cancel via aclose
        import asyncio

        await asyncio.sleep(0.05)
        await bus.aclose()

    assert started["event_name"] == "call.session.ended"
    assert started["consumer_group"] == "gid_qc_call_session_ended"
