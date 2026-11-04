"""T12：RocketMQ receive 异常不得终止订阅，退避后继续消费。"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from common.eventbus.rocketmq import (
    RocketMQEventBus,
    _receive_backoff_seconds,
)

EVENT = "complaint.created"
GROUP = "gid_cs"


class _FlakyConsumer:
    """第一次 receive 抛异常，第二次返回一条消息，之后空转。"""

    def __init__(self) -> None:
        self.calls = 0
        self.acked: list[Any] = []

    def receive(self, *_a: Any, **_k: Any) -> list[Any]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("broker receive failed")
        if self.calls == 2:
            msg = MagicMock(seq=1)
            return [msg]
        return []

    def ack(self, msg: Any, *_a: Any, **_k: Any) -> None:
        self.acked.append(msg)

    def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_receive_error_does_not_stop_subscription():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    consumer = _FlakyConsumer()
    done = asyncio.Event()
    warnings: list[dict[str, Any]] = []

    def warning_spy(event: str, **kwargs: Any) -> None:
        if event == "eventbus_consumer_receive_failed":
            warnings.append(kwargs)

    async def fake_handle(msg, event_name, group, handler, consumer, loop):
        consumer.ack(msg)
        done.set()

    with (
        patch.object(bus, "_start_consumer", return_value=consumer),
        patch.object(bus, "_handle_message", new=fake_handle),
        patch("common.eventbus.rocketmq._receive_backoff_seconds", return_value=0.0),
        patch("common.eventbus.rocketmq.logger.warning", side_effect=warning_spy),
    ):
        task = asyncio.create_task(bus._consume_group(GROUP, {EVENT: MagicMock()}))
        await asyncio.wait_for(done.wait(), timeout=5)

        # 单次 receive 异常后订阅仍存活，并继续消费后续消息
        assert not task.done()
        assert consumer.acked and consumer.acked[0].seq == 1
        assert warnings and warnings[0]["consecutive_failures"] == 1
        assert warnings[0]["error_type"] == "RuntimeError"

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await bus.aclose()


@pytest.mark.asyncio
async def test_receive_recovery_logged_after_consecutive_failures():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")

    class _TwoFailuresConsumer(_FlakyConsumer):
        def receive(self, *_a: Any, **_k: Any) -> list[Any]:
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError(f"fail-{self.calls}")
            if self.calls == 3:
                return [MagicMock(seq=1)]
            return []

    consumer = _TwoFailuresConsumer()
    done = asyncio.Event()
    infos: list[dict[str, Any]] = []

    def info_spy(event: str, **kwargs: Any) -> None:
        if event == "eventbus_consumer_receive_recovered":
            infos.append(kwargs)

    async def fake_handle(msg, event_name, group, handler, consumer, loop):
        consumer.ack(msg)
        done.set()

    with (
        patch.object(bus, "_start_consumer", return_value=consumer),
        patch.object(bus, "_handle_message", new=fake_handle),
        patch("common.eventbus.rocketmq._receive_backoff_seconds", return_value=0.0),
        patch("common.eventbus.rocketmq.logger.info", side_effect=info_spy),
    ):
        task = asyncio.create_task(bus._consume_group(GROUP, {EVENT: MagicMock()}))
        await asyncio.wait_for(done.wait(), timeout=5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await bus.aclose()

    assert infos and infos[0]["failures"] == 2


def test_receive_backoff_is_exponential_and_capped():
    assert _receive_backoff_seconds(1) == 1.0
    assert _receive_backoff_seconds(2) == 2.0
    assert _receive_backoff_seconds(5) == 16.0
    assert _receive_backoff_seconds(6) == 30.0
    assert _receive_backoff_seconds(100) == 30.0


@pytest.mark.asyncio
async def test_consumer_startup_failure_does_not_stop_subscription():
    """Q-001 裁决（纳入 T12 边界）：consumer 启动失败退避重试，不静默终止订阅。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    consumer = _FlakyConsumer()  # receive 第 1 次抛异常、第 2 次返回消息
    start_calls = {"n": 0}

    def flaky_start(_event_name: str, _group: str):
        start_calls["n"] += 1
        if start_calls["n"] <= 2:
            raise RuntimeError("proxy startup failed")
        return consumer

    done = asyncio.Event()
    warnings: list[dict[str, Any]] = []
    infos: list[dict[str, Any]] = []

    def warning_spy(event: str, **kwargs: Any) -> None:
        if event == "eventbus_consumer_start_failed":
            warnings.append(kwargs)

    def info_spy(event: str, **kwargs: Any) -> None:
        if event == "eventbus_consumer_start_recovered":
            infos.append(kwargs)

    async def fake_handle(msg, event_name, group, handler, consumer, loop):
        consumer.ack(msg)
        done.set()

    with (
        patch.object(bus, "_start_consumer", side_effect=flaky_start),
        patch.object(bus, "_handle_message", new=fake_handle),
        patch("common.eventbus.rocketmq._receive_backoff_seconds", return_value=0.0),
        patch("common.eventbus.rocketmq.logger.warning", side_effect=warning_spy),
        patch("common.eventbus.rocketmq.logger.info", side_effect=info_spy),
    ):
        task = asyncio.create_task(bus._consume_group(GROUP, {EVENT: MagicMock()}))
        await asyncio.wait_for(done.wait(), timeout=5)

        assert not task.done(), "启动失败重试成功后订阅必须存活"
        assert start_calls["n"] == 3
        assert [w["consecutive_failures"] for w in warnings] == [1, 2]
        assert infos and infos[0]["failures"] == 2
        assert consumer.acked and consumer.acked[0].seq == 1

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await bus.aclose()
