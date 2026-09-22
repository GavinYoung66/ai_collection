"""QC 韧性 hooks 适配器：记录 qc.* 事件，不进 M1 六频道 Redis。"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

# 与 QcEventPublisher.emitted 隔离，避免走六频道 _publish / Redis PUBLISH
emitted: list[tuple[str, dict[str, Any]]] = []


async def on_resilience_event(name: str, payload: dict[str, Any]) -> None:
    """记录韧性事件（structlog + 可测列表）。失败 MUST NOT 抛异常阻断主流程。"""
    try:
        emitted.append((name, dict(payload)))
        logger.info("qc_resilience_event", name=name, payload=payload)
    except Exception as exc:  # noqa: BLE001 — 适配器失败不得阻断质检
        logger.warning("qc_resilience_adapter_failed", name=name, error=str(exc))


def clear_emitted() -> None:
    emitted.clear()
