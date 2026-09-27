"""质检事件发布。允许 6 频道；禁止 qc.escalate.human 与 qc.llm.degraded。

本 change **QC 链路三事件** 上总线范围：
- 入站（由 DP 发）：``call.session.ended``、``etl.task.done``
- 出站（本文件）：仅 ``qc.check.completed`` → ``_BUS_CHANNELS``（信封 data 仅白名单）

其余五出站频道仍走 Redis PUBLISH，**故意保留**（投诉链/告警等留给后续 change）；
不是遗漏迁移。``emitted`` 仍保留完整业务 payload 供进程内观测。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import UUID, uuid4

from common.errors.codes import ErrorCode
from common.security.mask import mask_text
from common.trace.middleware import get_trace_id

CHANNEL_SYSTEM_ALERT = "system.alert.raised"
CHANNEL_ALERT_RAISED = "qc.alert.raised"
CHANNEL_CHECK_COMPLETED = "qc.check.completed"
CHANNEL_COMPLAINT_SUSPECTED = "qc.complaint.suspected"
CHANNEL_STOP_CALL = "qc.stop_call.detected"
CHANNEL_REVIEW_DONE = "qc.review.done"
ALERT_STREAM = "alert:stream"
# Bus-migrated channels (envelope on wire; full payload kept in ``emitted``).
_BUS_CHANNELS = frozenset({CHANNEL_CHECK_COMPLETED})

ALLOWED_CHANNELS = frozenset(
    {
        CHANNEL_ALERT_RAISED,
        CHANNEL_CHECK_COMPLETED,
        CHANNEL_COMPLAINT_SUSPECTED,
        CHANNEL_STOP_CALL,
        CHANNEL_REVIEW_DONE,
        CHANNEL_SYSTEM_ALERT,
    }
)
FORBIDDEN_CHANNELS = frozenset({"qc.llm.degraded", "qc.escalate.human"})

REVIEW_SAMPLE_TYPE = {
    "CONFIRM": "positive",
    "DISMISS": "negative",
    "CORRECT": "corrected",
}

COMPLETED_KEYS = frozenset(
    {"qc_record_id", "session_id", "call_id", "case_id", "score", "risk", "grade", "trace_id", "ts"}
)
COMPLETED_FORBIDDEN_KEYS = frozenset({"qc_result", "qc_status", "skipped_rules", "compliance_score", "risk_level"})


def event_call_id(call_id: str | None, *, session_id: str | None = None) -> str | None:
    """QCSession.call_id UUID 或 JSON null；禁止用 session_id / replay 路径冒充。"""
    if call_id is None or str(call_id).strip() == "":
        return None
    text = str(call_id)
    if session_id is not None and text == str(session_id):
        return None
    try:
        return str(UUID(text))
    except ValueError:
        return None


def sample_type_for(action: str) -> str:
    mapped = REVIEW_SAMPLE_TYPE.get(action)
    if mapped is None:
        raise ValueError(f"qc.review.done sample_type only positive|negative|corrected via CONFIRM/DISMISS/CORRECT, got {action!r}")
    return mapped


class QcEventPublisher:
    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis
        self.emitted: list[tuple[str, dict[str, Any]]] = []
        self.streams: list[tuple[str, dict[str, str]]] = []

    def _base(self, *, trace_id: str | None = None, ts: int | None = None) -> dict[str, Any]:
        return {
            "trace_id": trace_id if trace_id is not None else (get_trace_id() or ""),
            "ts": int(ts if ts is not None else time.time()),
        }

    async def _publish_via_bus(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish whitelist ``data`` via EventBus; reuse injected redis in redis mode."""
        from common.eventbus.factory import get_event_bus
        from common.eventbus.redis_pubsub import RedisPubSubEventBus
        from common.eventbus.util import whitelist_data

        if not (os.getenv("EVENT_BUS") or "").strip():
            os.environ["EVENT_BUS"] = "redis"
        if not (os.getenv("EVENT_BUS_SVC") or "").strip():
            os.environ["EVENT_BUS_SVC"] = "qc"

        data = whitelist_data(payload)
        key = (
            str(payload["session_id"])
            if payload.get("session_id")
            else (str(payload["case_id"]) if payload.get("case_id") else None)
        )
        trace_id = str(payload.get("trace_id") or "") or None
        mode = (os.getenv("EVENT_BUS") or "redis").strip().lower()
        # Prefer injected redis for redis/grayscale (and pytest MemoryRedis).
        # Only skip when explicitly on rocketmq / redis_streams.
        if self._redis is not None and mode not in ("rocketmq", "redis_streams"):
            bus = RedisPubSubEventBus(redis=self._redis)
            await bus.publish(channel, data, key=key, trace_id=trace_id)
            return
        # Reuse process-wide singleton; factory handles lifecycle + shutdown.
        bus = get_event_bus()
        await bus.publish(channel, data, key=key, trace_id=trace_id)

    async def _publish(self, channel: str, payload: dict[str, Any]) -> dict[str, Any]:
        if channel in FORBIDDEN_CHANNELS:
            raise ValueError(f"forbidden channel: {channel}")
        if channel not in ALLOWED_CHANNELS:
            raise ValueError(f"channel not in M1 allow-list: {channel}")
        # Business payload list unchanged (score/risk/grade still visible to callers).
        self.emitted.append((channel, payload))
        if channel in _BUS_CHANNELS:
            await self._publish_via_bus(channel, payload)
            return payload
        if self._redis is not None:
            await self._redis.publish(channel, json.dumps(payload, ensure_ascii=False))
        return payload

    async def publish_alert_raised(
        self,
        *,
        alert_id: str,
        session_id: str,
        call_id: str | None,
        level: str,
        evidence: str | None,
        trace_id: str | None = None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "alert_id": alert_id,
            "session_id": session_id,
            "call_id": event_call_id(call_id, session_id=session_id),
            "level": level,
            "evidence": mask_text(evidence or ""),
            **self._base(trace_id=trace_id, ts=ts),
        }
        await self._publish(CHANNEL_ALERT_RAISED, payload)
        fields = {key: json.dumps(value, ensure_ascii=False) for key, value in payload.items()}
        self.streams.append((ALERT_STREAM, fields))
        xadd = getattr(self._redis, "xadd", None) if self._redis is not None else None
        if xadd is not None:
            await xadd(ALERT_STREAM, fields)
        return payload

    async def publish_check_completed(
        self,
        *,
        qc_record_id: str,
        session_id: str,
        call_id: str | None,
        case_id: str | None,
        score: int | None,
        risk: str | None,
        grade: str | None,
        trace_id: str | None = None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "qc_record_id": qc_record_id,
            "session_id": session_id,
            "call_id": event_call_id(call_id, session_id=session_id),
            "case_id": case_id,
            "score": score,
            "risk": risk,
            "grade": grade,
            **self._base(trace_id=trace_id, ts=ts),
        }
        extra = set(payload) - COMPLETED_KEYS
        if extra:
            raise ValueError(f"unexpected completed keys: {extra}")
        if COMPLETED_FORBIDDEN_KEYS & set(payload):
            raise ValueError("completed payload must not include qc_result/qc_status/skipped_rules")
        return await self._publish(CHANNEL_CHECK_COMPLETED, payload)

    async def publish_complaint_suspected(
        self,
        *,
        qc_record_id: str,
        session_id: str,
        case_id: str | None,
        redline_tags: list[str] | None = None,
        trace_id: str | None = None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        if not qc_record_id:
            raise ValueError("qc.complaint.suspected requires qc_record_id")
        payload = {
            "qc_record_id": qc_record_id,
            "session_id": session_id,
            "case_id": case_id,
            "redline_tags": list(redline_tags or []),
            **self._base(trace_id=trace_id, ts=ts),
        }
        return await self._publish(CHANNEL_COMPLAINT_SUSPECTED, payload)

    async def publish_stop_call_detected(
        self,
        *,
        session_id: str,
        case_id: str | None,
        evidence: str | None,
        trace_id: str | None = None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "session_id": session_id,
            "case_id": case_id,
            "evidence": mask_text(evidence or ""),
            **self._base(trace_id=trace_id, ts=ts),
        }
        return await self._publish(CHANNEL_STOP_CALL, payload)

    async def publish_review_done(
        self,
        *,
        qc_record_id: str,
        action: str,
        revised_score: int | None = None,
        redline_tags: list[str] | None = None,
        evidence_masked: str | None = None,
        trace_id: str | None = None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "qc_record_id": qc_record_id,
            "action": action,
            "revised_score": revised_score,
            "redline_tags": list(redline_tags or []),
            "evidence_masked": mask_text(evidence_masked or ""),
            "sample_type": sample_type_for(action),
            **self._base(trace_id=trace_id, ts=ts),
        }
        if payload["sample_type"] not in {"positive", "negative", "corrected"}:
            raise ValueError("invalid sample_type")
        return await self._publish(CHANNEL_REVIEW_DONE, payload)

    async def publish_system_alert_raised(
        self,
        *,
        event: str = "qc:llm_degraded",
        error_code: str = ErrorCode.A2011.value,
        service: str = "qc-service",
        level: str = "high",
        description: str = "LLM 审查熔断，已跳过语义复核",
        alert_id: str | None = None,
        trace_id: str | None = None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        if event != "qc:llm_degraded":
            raise ValueError("1.4 仅允许 event=qc:llm_degraded")
        payload = {
            "alert_id": alert_id or str(uuid4()),
            "level": level,
            "event": event,
            "service": service,
            "description": description,
            "error_code": error_code,
            **self._base(trace_id=trace_id, ts=ts),
        }
        return await self._publish(CHANNEL_SYSTEM_ALERT, payload)
