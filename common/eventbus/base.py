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


def consumer_group_name(svc: str, event_name: str) -> str:
    """Build ``gid_{svc}_{event}`` (event dots → underscores)."""
    return f"gid_{svc}_{event_name.replace('.', '_')}"


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
