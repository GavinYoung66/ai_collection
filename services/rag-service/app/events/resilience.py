"""RAG 韧性 hooks 适配器：记录 rag.* 事件，不进投诉六频道 Redis。"""
from __future__ import annotations

from collections import deque
from typing import Any

import structlog

logger = structlog.get_logger()

# 与投诉 EventPublisher / 六频道 PUBLISH 隔离，仅 structlog + 可测列表。
# T102（spec runtime-correctness：常驻进程内存有界）：容量上限显式常量——
# deque(maxlen) 环形截断后 10 万次事件长度稳定在上限内，RSS 不线性增长。
_EMITTED_MAXLEN = 1000
emitted: deque[tuple[str, dict[str, Any]]] = deque(maxlen=_EMITTED_MAXLEN)


async def on_resilience_event(name: str, payload: dict[str, Any]) -> None:
    """记录韧性事件（structlog + 可测列表）。失败 MUST NOT 抛异常阻断主流程。

    预期事件名：``rag.rate_limit.hit`` / ``rag.circuit_breaker.open`` /
    ``rag.degrade.triggered``（及零件发出的同前缀变体）。
    """
    try:
        emitted.append((name, dict(payload)))
        logger.info("rag_resilience_event", name=name, payload=payload)
    except Exception as exc:  # noqa: BLE001 — 适配器失败不得阻断检索/入库
        logger.warning("rag_resilience_adapter_failed", name=name, error=str(exc))


def clear_emitted() -> None:
    emitted.clear()
