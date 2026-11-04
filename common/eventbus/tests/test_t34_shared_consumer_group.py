"""T34：共享消费组 + tag 事件过滤单测。

验收口径：
- 消费组命名 ``gid_{svc}``，与事件类型无关，数量不随事件数增长；
- 同一服务多次 subscribe 复用同一个 consumer / 同一个消费循环；
- 订阅 tag 合并为一次过滤表达式，消息按 tag 分派到对应 handler；
- 未登记 tag 只 ack 不处理，避免毒消息无限重投。
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from common.eventbus.base import consumer_group_name
from common.eventbus.rocketmq import RocketMQEventBus

EVENT_A = "complaint.created"
EVENT_B = "call.session.ended"


class _ScriptedConsumer:
    def __init__(self, batches: list[list[Any]] | None = None) -> None:
        self.batches = batches or []
        self.calls = 0
        self.acked: list[Any] = []
        self.shutdown_called = False

    def receive(self, *_a: Any, **_k: Any) -> list[Any]:
        self.calls += 1
        if not self.batches:
            return []
        return self.batches.pop(0)

    def ack(self, msg: Any, *_a: Any, **_k: Any) -> None:
        self.acked.append(msg)

    def shutdown(self) -> None:
        self.shutdown_called = True


def _msg(tag: str) -> MagicMock:
    message = MagicMock()
    message.tag = tag
    return message


def test_group_name_is_shared_across_events():
    assert consumer_group_name("qc") == "gid_qc"
    assert consumer_group_name("qc") != consumer_group_name("dp")


@pytest.mark.asyncio
async def test_multiple_subscribes_share_one_consumer():
    """3 个事件 → 1 个消费组、1 个 consumer、1 个消费循环。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    consumer = _ScriptedConsumer()
    start_calls: list[tuple[str, ...]] = []

    def _start(tags: tuple[str, ...], group: str) -> _ScriptedConsumer:
        start_calls.append(tags)
        return consumer

    with patch.object(bus, "_start_consumer", side_effect=_start):
        await bus.subscribe(EVENT_A, "cs", MagicMock())
        await bus.subscribe(EVENT_B, "cs", MagicMock())
        await bus.subscribe("etl.task.done", "cs", MagicMock())
        await asyncio.sleep(0.05)

        assert len(bus._subscriptions) == 1, "消费组数量不得随事件数增长"
        assert list(bus._subscriptions) == ["gid_cs"]
        await bus.aclose()

    assert len(start_calls) == 1
    assert set(start_calls[0]) == {EVENT_A, EVENT_B, "etl.task.done"}
    assert consumer.shutdown_called


@pytest.mark.asyncio
async def test_tag_filter_expression_is_union_of_events():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    captured: dict[str, Any] = {}

    class _FakeFilterExpression:
        def __init__(self, expression: str) -> None:
            captured["expression"] = expression

    class _FakeSimpleConsumer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["subscription"] = kwargs.get("subscription")
            captured["group"] = args[1] if len(args) > 1 else kwargs.get("consumer_group")

        def startup(self) -> None:
            captured["started"] = True

    fake_module = MagicMock()
    fake_module.ClientConfiguration = MagicMock(return_value=object())
    fake_module.Credentials = MagicMock(return_value=object())
    fake_module.FilterExpression = _FakeFilterExpression
    fake_module.SimpleConsumer = _FakeSimpleConsumer

    with patch.dict("sys.modules", {"rocketmq": fake_module}):
        consumer = bus._start_consumer((EVENT_A, EVENT_B), "gid_cs")

    assert captured["expression"] == f"{EVENT_A} || {EVENT_B}"
    assert captured["group"] == "gid_cs"
    assert captured["started"] is True
    assert consumer is not None


@pytest.mark.asyncio
async def test_message_dispatched_by_tag():
    """同组多事件：按消息 tag 命中各自 handler。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    consumer = _ScriptedConsumer(
        batches=[[_msg(EVENT_A), _msg(EVENT_B)]]
    )
    handled: list[str] = []

    async def _handle(msg, event_name, group, handler, consumer_arg, loop):
        handled.append(event_name)
        consumer.ack(msg)

    with patch.object(bus, "_start_consumer", return_value=consumer), \
         patch.object(bus, "_handle_message", new=_handle):
        task = asyncio.create_task(
            bus._consume_group("gid_cs", {EVENT_A: MagicMock(), EVENT_B: MagicMock()})
        )
        for _ in range(50):
            if len(handled) >= 2:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await bus.aclose()

    assert handled == [EVENT_A, EVENT_B]


@pytest.mark.asyncio
async def test_unregistered_tag_is_acked_without_dispatch():
    """未登记 tag（理论上被 tag 过滤拦截）：只 ack，不调用 handler、不重投。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    stray = _msg("unknown.event")
    consumer = _ScriptedConsumer(batches=[[stray]])
    handled: list[str] = []

    async def _handle(msg, event_name, group, handler, consumer_arg, loop):
        handled.append(event_name)

    with patch.object(bus, "_start_consumer", return_value=consumer), \
         patch.object(bus, "_handle_message", new=_handle):
        task = asyncio.create_task(
            bus._consume_group(
                "gid_cs", {EVENT_A: MagicMock(), EVENT_B: MagicMock()}
            )
        )
        for _ in range(50):
            if consumer.acked:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await bus.aclose()

    assert handled == []
    assert consumer.acked == [stray]


@pytest.mark.asyncio
async def test_subscribe_after_start_warns_restart_required():
    """consumer 启动后再订阅：tag 过滤无法热更新，必须告警并要求重启。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    consumer = _ScriptedConsumer()
    warnings: list[str] = []

    def _warning(event: str, **kwargs: Any) -> None:
        warnings.append(event)

    with patch.object(bus, "_start_consumer", return_value=consumer), \
         patch("common.eventbus.rocketmq.logger.warning", side_effect=_warning):
        await bus.subscribe(EVENT_A, "cs", MagicMock())
        await asyncio.sleep(0.05)  # 让消费循环进入 started 状态
        await bus.subscribe(EVENT_B, "cs", MagicMock())
        await bus.aclose()

    assert "eventbus_subscribe_after_consumer_start" in warnings
