"""事件发布（批次7 stub → 批次9 扩展为单真相源）。

禁止另建 app/service/event_publisher.py。
本批不接真实 Pub-Sub，内存列表可断言；后续替换为真实 MQ。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# 业务频道目录（仅文档性枚举，publish 不做强制校验）：
# 4 主频道 + 扩展 topic。韧性事件（csmp.rate_limit.hit / csmp.circuit_breaker.open /
# csmp.degrade.triggered 等，命名见 common.resilience.hooks）不经此清单，
# 由 hooks 适配器直接 publish 到同一 EventPublisher。
CHANNELS = (
    "complaint.judged",
    "complaint.closed",
    "complaint.insight.weekly",
    "complaint.mode.switched",
    "complaint.degraded",
    "case.risk.upgraded",
    "callback.requested",  # V4 凡投诉必回电
    "qc.violation.reported",  # V4 坐席违规质检
)


@dataclass
class EventRecord:
    topic: str
    payload: dict[str, Any]


@dataclass
class EventPublisher:
    """内存 stub publisher，线程安全；payload 强制含 trace_id。"""

    _events: list[EventRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        if "trace_id" not in payload:
            payload = {**payload, "trace_id": str(uuid.uuid4())}
        with self._lock:
            self._events.append(EventRecord(topic=topic, payload=payload))
        # 落 stdout 日志，使韧性事件（csmp.rate_limit.hit / csmp.circuit_breaker.open 等）
        # 在 docker logs 可检索可告警；日志失败绝不影响内存断言主流程
        try:
            logger.info(topic, **payload)
        except Exception:  # noqa: BLE001
            pass

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
