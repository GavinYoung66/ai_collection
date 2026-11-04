"""Shared helpers for EventBus publishers."""
from __future__ import annotations

from typing import Any

from common.eventbus.envelope import data_whitelist


def whitelist_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract envelope ``data`` fields only (configured ID whitelist)."""
    out: dict[str, Any] = {}
    for key in data_whitelist():
        if key in payload and payload[key] is not None:
            out[key] = payload[key]
    return out


def handler_payload(envelope_data: dict[str, Any], *, trace_id: str | None = None) -> dict[str, Any]:
    """Build a handler-facing dict from envelope data (+ optional trace_id)."""
    payload = dict(envelope_data)
    if trace_id and "trace_id" not in payload:
        payload["trace_id"] = trace_id
    return payload
