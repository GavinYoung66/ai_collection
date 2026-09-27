"""EventBus observability hooks: csmp.eventbus.{publish,consume,retry,dlq}."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from common.resilience.hooks import on_event

_HOOK_PUBLISH = "csmp.eventbus.publish"
_HOOK_CONSUME = "csmp.eventbus.consume"
_HOOK_RETRY = "csmp.eventbus.retry"
_HOOK_DLQ = "csmp.eventbus.dlq"


def _svc() -> str:
    return (
        os.getenv("EVENT_BUS_SVC")
        or os.getenv("SERVICE_NAME")
        or "csmp"
    )


def _base(
    *,
    event_id: str | None,
    event_name: str | None,
    trace_id: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": event_id,
        "event_name": event_name,
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "svc": _svc(),
    }
    if extra:
        payload.update(extra)
    return payload


async def emit_publish(
    *,
    event_id: str,
    event_name: str,
    trace_id: str,
) -> None:
    await on_event(
        _HOOK_PUBLISH,
        _base(event_id=event_id, event_name=event_name, trace_id=trace_id),
    )


async def emit_consume(
    *,
    event_id: str,
    event_name: str,
    trace_id: str,
) -> None:
    await on_event(
        _HOOK_CONSUME,
        _base(event_id=event_id, event_name=event_name, trace_id=trace_id),
    )


async def emit_retry(
    *,
    event_id: str,
    event_name: str,
    trace_id: str,
    attempt: int,
    max_attempt: int,
    error_type: str,
) -> None:
    await on_event(
        _HOOK_RETRY,
        _base(
            event_id=event_id,
            event_name=event_name,
            trace_id=trace_id,
            extra={
                "attempt": attempt,
                "max_attempt": max_attempt,
                "error_type": error_type,
            },
        ),
    )


async def emit_dlq(
    *,
    event_id: str,
    event_name: str,
    trace_id: str,
    consumer_group: str,
    last_error: str,
) -> None:
    await on_event(
        _HOOK_DLQ,
        _base(
            event_id=event_id,
            event_name=event_name,
            trace_id=trace_id,
            extra={
                "consumer_group": consumer_group,
                "last_error": last_error,
                "dlq_timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ),
    )
