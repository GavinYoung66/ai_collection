"""RocketMQ adapter unit tests (task 2.4, go path)."""
from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_receive_failure_emits_warning_and_continues():
    """T12：receive 异常记 warning 并退避续跑，不得冲出订阅循环。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    done = asyncio.Event()

    class _FlakyConsumer:
        def __init__(self):
            self.calls = 0

        def receive(self, *_a, **_k):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("proxy unavailable")
            if self.calls == 2:
                return [MagicMock(seq=1)]
            return []

        def ack(self, *_a, **_k):
            return None

        def shutdown(self):
            return None

    async def _fake_handle(msg, event_name, consumer_group, handler, consumer, loop):
        done.set()

    with patch.object(bus, "_start_consumer", return_value=_FlakyConsumer()), \
         patch.object(bus, "_handle_message", new=_fake_handle), \
         patch("common.eventbus.rocketmq._receive_backoff_seconds", return_value=0.0), \
         patch("common.eventbus.rocketmq.logger") as mock_logger:
        task = asyncio.create_task(
            bus._consume_loop("call.session.ended", "gid_qc_call_session_ended", MagicMock())
        )
        await asyncio.wait_for(done.wait(), timeout=5)
        assert not task.done(), "receive 异常后订阅循环不得退出"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await bus.aclose()

    warning_calls = [
        c for c in mock_logger.warning.call_args_list
        if c.args and c.args[0] == "eventbus_consumer_receive_failed"
    ]
    assert warning_calls, "receive 失败必须记录 eventbus_consumer_receive_failed"
    kwargs = warning_calls[0].kwargs
    assert kwargs["consumer_group"] == "gid_qc_call_session_ended"
    assert kwargs["error"] == "proxy unavailable"
