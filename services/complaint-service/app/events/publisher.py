"""事件发布：业务 topic → EventBus；韧性 hooks → 进程内 tap + structlog。

禁止另建 app/service/event_publisher.py。
``list_events`` 仍可供单测断言（含 hooks 与业务）；业务跨服务投递走 RocketMQ。
"""
from __future__ import annotations

import asyncio
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# 业务频道目录（跨服务 → EventBus）：
# 4 主频道 + 扩展 topic + result.ready。韧性事件（csmp.*）不上业务 Topic。
CHANNELS = (
    "complaint.judged",
    "complaint.closed",
    "complaint.insight.weekly",
    "complaint.mode.switched",
    "complaint.degraded",
    "complaint.result.ready",
    "case.risk.upgraded",
    "callback.requested",  # V4 凡投诉必回电
    "qc.violation.reported",  # V4 坐席违规质检
)

_BUS_TOPICS = frozenset(CHANNELS)
_HOOK_PREFIX = "csmp."


@dataclass
class EventRecord:
    topic: str
    payload: dict[str, Any]


@dataclass
class EventPublisher:
    """双轨 publisher：业务上总线；hooks 仅进程内 + structlog。"""

    _events: list[EventRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        if "trace_id" not in payload:
            payload = {**payload, "trace_id": str(uuid.uuid4())}
        with self._lock:
            self._events.append(EventRecord(topic=topic, payload=payload))
        try:
            logger.info(topic, **payload)
        except Exception:  # noqa: BLE001
            pass
        # Hooks 双轨：不上 csmp_business_events；业务 CHANNELS 上 EventBus
        if topic.startswith(_HOOK_PREFIX):
            return
        if topic in _BUS_TOPICS:
            self._schedule_bus_publish(topic, payload)

    def _schedule_bus_publish(self, topic: str, payload: dict[str, Any]) -> None:
        async def _go() -> None:
            try:
                if not (os.getenv("EVENT_BUS") or "").strip():
                    os.environ["EVENT_BUS"] = "rocketmq"
                if not (os.getenv("EVENT_BUS_SVC") or "").strip():
                    os.environ["EVENT_BUS_SVC"] = "cs"
                from common.eventbus.factory import get_event_bus
                from common.eventbus.util import whitelist_data

                bus = get_event_bus()
                key = (
                    str(payload["case_id"])
                    if payload.get("case_id")
                    else (
                        str(payload["complaint_no"])
                        if payload.get("complaint_no")
                        else None
                    )
                )
                await bus.publish(
                    topic,
                    whitelist_data(payload),
                    key=key,
                    trace_id=str(payload.get("trace_id") or "") or None,
                )
            except Exception as exc:  # noqa: BLE001 — bus must not break callers
                logger.warning(
                    "complaint_eventbus_publish_failed",
                    topic=topic,
                    error=str(exc),
                )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_go())
        except RuntimeError:
            try:
                asyncio.run(_go())
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "complaint_eventbus_publish_failed",
                    topic=topic,
                    error=str(exc),
                )

    def list_events(self, topic: str | None = None) -> list[EventRecord]:
        with self._lock:
            if topic is None:
                return list(self._events)
            return [e for e in self._events if e.topic == topic]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


# 全局单例（测试可替换/断言）
publisher = EventPublisher()
# 兼容别名（旧代码若引用 event_publisher）
event_publisher = publisher
