"""Local verification for QC-chain events via mock EventBus Protocol."""
from __future__ import annotations

from typing import Any

import pytest

from common.eventbus.envelope import EventEnvelope, build_envelope
from common.eventbus.util import whitelist_data


class _MockEventBus:
    """In-memory EventBus Protocol stand-in (no redis / factory)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}
        self.published: list[EventEnvelope] = []

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
        self.published.append(env)
        for handler in self._handlers.get(event_name, []):
            await handler(env)

    async def subscribe(
        self, event_name: str, group: str, handler: Any
    ) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_etl_and_completed_roundtrip_via_eventbus():
    bus = _MockEventBus()

    etl_got: list[EventEnvelope] = []
    completed_got: list[EventEnvelope] = []

    async def on_etl(env: EventEnvelope) -> None:
        etl_got.append(env)

    async def on_completed(env: EventEnvelope) -> None:
        completed_got.append(env)

    await bus.subscribe("etl.task.done", "qc", on_etl)
    await bus.subscribe("qc.check.completed", "dp", on_completed)

    etl_payload = {
        "case_id": "c1",
        "session_id": "s1",
        "backfill_date": "2026-09-27",
        "trace_id": "etl-c1",
        "ts": 1,
    }
    await bus.publish(
        "etl.task.done",
        whitelist_data(etl_payload),
        key="s1",
        trace_id="etl-c1",
    )

    completed_payload = {
        "qc_record_id": "q1",
        "session_id": "s1",
        "case_id": "c1",
        "call_id": None,
        "score": 80,
        "risk": "high",
        "grade": "PASS",
        "trace_id": "tr1",
        "ts": 2,
    }
    await bus.publish(
        "qc.check.completed",
        whitelist_data(completed_payload),
        key="s1",
        trace_id="tr1",
    )

    await bus.aclose()
    assert len(etl_got) == 1
    assert etl_got[0].data["session_id"] == "s1"
    assert etl_got[0].data["backfill_date"] == "2026-09-27"
    assert "ts" not in etl_got[0].data

    assert len(completed_got) == 1
    assert completed_got[0].data["qc_record_id"] == "q1"
    assert "score" not in completed_got[0].data
    assert "risk" not in completed_got[0].data
