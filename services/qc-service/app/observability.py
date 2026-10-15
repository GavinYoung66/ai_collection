"""QC-10.2 埋点：structlog 事件名，不是 Redis 频道 PUBLISH。"""
from __future__ import annotations

import time
from typing import Any

import structlog

from common.trace.middleware import get_trace_id

logger = structlog.get_logger("qc.metrics")

STALE_SLA_SECONDS = 60
STALE_AFTER_SECONDS = 2 * 3600

PROCESS_STARTED_AT = time.time()
_last_success_load_at: float | None = None
_mismatch_since: float | None = None
_events: list[dict[str, Any]] = []

FORBIDDEN_METRIC_EVENTS = frozenset({"qc.escalate.triggered"})


def captured_events() -> list[dict[str, Any]]:
    return list(_events)


def reset_events() -> None:
    _events.clear()


def last_success_load_at() -> float:
    return _last_success_load_at if _last_success_load_at is not None else PROCESS_STARTED_AT


def mark_words_success(now: float | None = None) -> None:
    global _last_success_load_at
    _last_success_load_at = now if now is not None else time.time()


def emit(event: str, **fields: Any) -> dict[str, Any]:
    if event in FORBIDDEN_METRIC_EVENTS:
        raise ValueError(f"forbidden metric event: {event}")
    if fields.get("mode") == "realtime":
        raise ValueError("mode=realtime is forbidden")
    payload = {
        "event": event,
        "trace_id": fields.pop("trace_id", None) or get_trace_id() or "",
        **fields,
    }
    _events.append(payload)
    log_fields: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "event":
            continue
        if key == "level":
            log_fields["alert_level"] = value
        else:
            log_fields[key] = value
    logger.info(event, **log_fields)
    return payload


def emit_check_started(*, session_id: str, call_id: str | None) -> dict[str, Any]:
    return emit("qc.check.started", session_id=session_id, call_id=call_id, mode="offline")


def emit_wait_text(*, session_id: str) -> dict[str, Any]:
    return emit("qc.check.wait_text", session_id=session_id)


def emit_check_completed(
    *,
    session_id: str,
    call_id: str | None,
    latency_ms: int,
    score: int | None,
    risk: str | None,
    grade: str | None,
    violation_count: int,
) -> dict[str, Any]:
    """日志事件名 qc.check.completed；MUST NOT Redis PUBLISH 该频道。

    妥协记录（qc-deduction-scoring 批次2）：`score` 键名保留现网结构不变，
    值口径已改为扣分（deduction_total）；design 未对该 structlog 键另行约定。
    """
    return emit(
        "qc.check.completed",
        session_id=session_id,
        call_id=call_id,
        latency_ms=latency_ms,
        score=score,
        risk=risk,
        grade=grade,
        violation_count=violation_count,
    )


def emit_llm_review_started(*, prompt_version: str, turn_count: int) -> dict[str, Any]:
    return emit("llm.review.started", prompt_version=prompt_version, turn_count=turn_count)


def emit_llm_review_completed(
    *,
    latency_ms: int,
    token_usage: int | None = None,
    hit_count: int = 0,
    status: str = "ok",
) -> dict[str, Any]:
    return emit(
        "llm.review.completed",
        latency_ms=latency_ms,
        token_usage=token_usage,
        hit_count=hit_count,
        status=status,
    )


def emit_llm_review_degraded(*, reason: str) -> dict[str, Any]:
    return emit("llm.review.degraded", reason=reason)


def emit_words_loaded(*, version: str | None, word_count: int) -> dict[str, Any]:
    mark_words_success()
    return emit("rag.words.loaded", version=version, word_count=word_count)


def emit_alert_raised(
    *,
    call_id: str | None,
    level: str,
    evidence: str | None = None,
) -> dict[str, Any]:
    return emit("qc.alert.raised", call_id=call_id, level=level, evidence=evidence)


def evaluate_words_stale(
    *,
    now: float | None = None,
    last_load: float | None = None,
    fetch_failing: bool = False,
    qc_version: str | None = None,
    rag_version: str | None = None,
    emit_log: bool = True,
) -> bool:
    """DD-02 §15.3：60s 热加载 SLA / 2h 陈旧。本轮可调用判定+日志，不订阅 kb.sensitive_updated。"""
    global _mismatch_since
    ts = now if now is not None else time.time()
    loaded = last_load if last_load is not None else last_success_load_at()
    stale_hours = (ts - loaded) / 3600.0
    stale = False
    if fetch_failing:
        stale = True
    if ts - loaded > STALE_AFTER_SECONDS:
        stale = True
    if qc_version is not None and rag_version is not None and qc_version != rag_version:
        if _mismatch_since is None:
            _mismatch_since = ts
        if ts - _mismatch_since > STALE_AFTER_SECONDS:
            stale = True
    else:
        _mismatch_since = None
    if stale and emit_log:
        emit(
            "rag.words.stale",
            stale_hours=round(stale_hours, 4),
            rag_version=rag_version,
            qc_version=qc_version,
        )
    return stale
