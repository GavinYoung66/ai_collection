"""EventBus Protocol — minimal publish/subscribe surface."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from common.eventbus.envelope import EventEnvelope

EventHandler = Callable[[EventEnvelope], Awaitable[None]]

# Physical topic for RocketMQ / logical stream namespace for streams adapter.
PHYSICAL_TOPIC = "csmp_business_events"

# RocketMQ dead-letter topic prefix (framework / app-produced).
DLQ_TOPIC_PREFIX = "%DLQ%"


def consumer_group_name(svc: str) -> str:
    """Build the shared consumer group ``gid_{svc}``.

    消费组按服务收敛（而非“服务×事件”），事件区分交给订阅 tag 过滤，
    因此消费组数量不随事件类型数量增长（T34）。
    """
    return f"gid_{svc}"


def dlq_topic_name(consumer_group: str) -> str:
    """``%DLQ%{consumer_group}`` topic name."""
    return f"{DLQ_TOPIC_PREFIX}{consumer_group}"


@runtime_checkable
class EventBus(Protocol):
    async def publish(
        self,
        event_name: str,
        data: dict[str, Any],
        *,
        key: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
    ) -> None: ...

    async def subscribe(
        self, event_name: str, group: str, handler: EventHandler
    ) -> None: ...
