"""Event envelope contract — single source of truth for bus payloads."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

logger = structlog.get_logger(__name__)

DATA_WHITELIST: frozenset[str] = frozenset(
    {
        "case_id",
        "session_id",
        "qc_record_id",
        "complaint_no",
        "backfill_date",
    }
)


def _rejected_fields(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    return sorted(set(data.keys()) - DATA_WHITELIST)


def _log_reject(
    *,
    event_id: str | None,
    event_name: str | None,
    trace_id: str | None,
    rejected: list[str],
    reason: str,
) -> None:
    logger.warning(
        "event_envelope_data_rejected",
        event_id=event_id,
        event_name=event_name,
        trace_id=trace_id,
        rejected_field=rejected[0] if rejected else None,
        rejected_fields=rejected or None,
        reason=reason,
    )


class EventEnvelope(BaseModel):
    """Unified event envelope; ``data`` only allows ID whitelist fields."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_name: str
    occurred_at: datetime
    trace_id: str
    data: dict[str, Any]

    @field_validator("data")
    @classmethod
    def validate_data_whitelist(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("data must be a dict")
        rejected = _rejected_fields(value)
        if rejected:
            # Full audit with event_id/trace_id is emitted by build_envelope /
            # parse_envelope wrappers (validator has no sibling-field access).
            raise ValueError(
                f"data contains non-whitelist fields: {rejected}; "
                f"allowed={sorted(DATA_WHITELIST)}"
            )
        return value


def build_envelope(
    event_name: str,
    data: dict[str, Any],
    *,
    event_id: str | None = None,
    trace_id: str | None = None,
    occurred_at: datetime | None = None,
) -> EventEnvelope:
    """Build a validated envelope (raises on illegal ``data``)."""
    eid = event_id or f"evt_{uuid.uuid4().hex}"
    tid = trace_id or f"tr_{uuid.uuid4().hex}"
    rejected = _rejected_fields(data)
    if rejected:
        _log_reject(
            event_id=eid,
            event_name=event_name,
            trace_id=tid,
            rejected=rejected,
            reason="non_whitelist_data_field",
        )
    try:
        return EventEnvelope(
            event_id=eid,
            event_name=event_name,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            trace_id=tid,
            data=data,
        )
    except (ValidationError, ValueError, TypeError):
        if not rejected:
            _log_reject(
                event_id=eid,
                event_name=event_name,
                trace_id=tid,
                rejected=[],
                reason="envelope_validation_failed",
            )
        raise


def peek_reject_meta(
    raw: Any,
) -> tuple[str | None, str | None, list[str]]:
    """Best-effort ``(event_id, trace_id, rejected_fields)`` from a raw payload.

    Used by consume-side reject logs so ``eventbus_consume_reject`` carries the
    same identity fields as ``event_envelope_data_rejected`` even when parse fails.
    """
    try:
        body: Any = raw
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8")
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict):
            return None, None, []
        eid = body.get("event_id") if isinstance(body.get("event_id"), str) else None
        tid = body.get("trace_id") if isinstance(body.get("trace_id"), str) else None
        return eid, tid, _rejected_fields(body.get("data"))
    except Exception:  # noqa: BLE001 — best-effort audit only
        return None, None, []


def parse_envelope(raw: Any, *, fallback_event_name: str | None = None) -> EventEnvelope:
    """Parse JSON/dict into EventEnvelope; audit-log rejects with ids when known."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        body: Any = json.loads(raw)
    else:
        body = raw
    if not isinstance(body, dict):
        _log_reject(
            event_id=None,
            event_name=fallback_event_name,
            trace_id=None,
            rejected=[],
            reason="envelope_not_object",
        )
        raise ValueError("envelope payload must be a JSON object")

    eid = body.get("event_id")
    tid = body.get("trace_id")
    ename = body.get("event_name") or fallback_event_name
    rejected = _rejected_fields(body.get("data"))
    if rejected:
        _log_reject(
            event_id=eid if isinstance(eid, str) else None,
            event_name=ename if isinstance(ename, str) else None,
            trace_id=tid if isinstance(tid, str) else None,
            rejected=rejected,
            reason="non_whitelist_data_field",
        )
    try:
        return EventEnvelope.model_validate(body)
    except (ValidationError, ValueError, TypeError):
        if not rejected:
            _log_reject(
                event_id=eid if isinstance(eid, str) else None,
                event_name=ename if isinstance(ename, str) else None,
                trace_id=tid if isinstance(tid, str) else None,
                rejected=[],
                reason="envelope_validation_failed",
            )
        raise
