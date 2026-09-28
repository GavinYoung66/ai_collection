"""Spec: at-least-once delivery — subscribers SHALL be idempotent via ``event_id``."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from common.eventbus.envelope import EventEnvelope, build_envelope


class _MockEventBus:
    """In-memory EventBus Protocol stand-in (no redis / factory)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}

    async def publish(
        self,
        event_name: str,
        data: dict[str, Any],
        *,
        key: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
    ) -> None:
        env = build_envelope(
            event_name,
            data,
            event_id=event_id,
            trace_id=trace_id or "tr_mock",
        )
        for handler in self._handlers.get(event_name, []):
            await handler(env)

    async def subscribe(
        self, event_name: str, group: str, handler: Any
    ) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_duplicate_event_id_processed_once_by_idempotent_handler():
    """Same ``event_id`` redelivered → handler processes once (spec 幂等消费)."""
    bus = _MockEventBus()
    seen: set[str] = set()
    outcomes: list[str] = []

    async def idempotent_handler(envelope: EventEnvelope) -> None:
        if envelope.event_id in seen:
            outcomes.append("skip")
            return
        seen.add(envelope.event_id)
        outcomes.append("process")

    await bus.subscribe("etl.task.done", "qc", idempotent_handler)

    await bus.publish(
        "etl.task.done",
        {"session_id": "s-dup", "case_id": "c-dup", "backfill_date": "2026-09-27"},
        key="s-dup",
        event_id="evt_dup_fixed",
        trace_id="tr_dup",
    )
    # Simulate at-least-once redelivery of the identical event_id.
    await bus.publish(
        "etl.task.done",
        {"session_id": "s-dup", "case_id": "c-dup", "backfill_date": "2026-09-27"},
        key="s-dup",
        event_id="evt_dup_fixed",
        trace_id="tr_dup",
    )

    await bus.aclose()
    assert outcomes == ["process", "skip"]
    assert seen == {"evt_dup_fixed"}


@pytest.mark.asyncio
async def test_envelope_exposes_event_id_for_dedup():
    """EventBus SHALL expose ``event_id`` so subscribers can dedupe."""
    env = build_envelope(
        "qc.check.completed",
        {"qc_record_id": "q1", "session_id": "s1", "case_id": "c1"},
        event_id="evt_idem_1",
        trace_id="tr_idem_1",
    )
    assert env.event_id == "evt_idem_1"
    assert env.trace_id == "tr_idem_1"
