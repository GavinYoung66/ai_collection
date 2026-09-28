"""qc.check.completed + complaint.result.ready 订阅器（data-platform）。

收到 qc.check.completed（经统一 EventBus）后：
1. 确认 qc_records 已落库（真开检）
2. HTTP GET /api/v1/qc/records/{session_id} 反拉 score/risk/grade
3. 更新快照质检列

收到 complaint.result.ready：确认日志（可选反拉校验）；SHALL NOT 代写投诉列。

质检链路与投诉链路独立：SHALL NOT 自动创建投诉工单（min-loop delta）。
不订阅 qc.complaint.suspected 建单。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.core.config import dp_settings
from app.core.redis import get_redis
from app.integrations.qc_client import QcClient
from app.services.snapshot import (
    check_has_high_alert,
    qc_record_exists,
    update_qc_cols,
)
from common.eventbus.envelope import EventEnvelope

logger = logging.getLogger(__name__)

CHANNEL_QC_CHECK_COMPLETED = "qc.check.completed"
CHANNEL_QC_COMPLAINT_SUSPECTED = "qc.complaint.suspected"  # 不订阅此频道建单
CHANNEL_COMPLAINT_RESULT_READY = "complaint.result.ready"

_WATCHDOG_INTERVAL_SECONDS = 30.0


def parse_event_payload(raw: Any) -> dict[str, Any]:
    """解析消息 payload（兼容单测直传 dict / JSON 字符串）。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("qc.check.completed payload is not JSON")
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def _enrich_qc_payload_from_http(payload: dict[str, Any]) -> dict[str, Any]:
    """信封 data 仅含定位字段时，HTTP 反拉 score/risk/grade/qc_record_id。"""
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return payload
    # 已有完整评分则跳过（单测直传兼容）
    if payload.get("score") is not None and payload.get("risk") is not None:
        return payload

    detail = await QcClient().get_record(session_id)
    if not detail:
        return payload

    enriched = dict(payload)
    if enriched.get("qc_record_id") in (None, "") and detail.get("qc_record_id"):
        enriched["qc_record_id"] = detail["qc_record_id"]
    if enriched.get("case_id") in (None, "") and detail.get("case_id"):
        enriched["case_id"] = detail["case_id"]
    # API 用 compliance_score；事件/快照映射键为 score
    score = detail.get("compliance_score", detail.get("score"))
    if enriched.get("score") is None and score is not None:
        enriched["score"] = score
    if enriched.get("risk") is None and detail.get("risk") is not None:
        enriched["risk"] = detail["risk"]
    if enriched.get("grade") is None and detail.get("grade") is not None:
        enriched["grade"] = detail["grade"]
    return enriched


async def _update_snapshot_qc_cols(case_id: str, payload: dict[str, Any]) -> bool:
    """用 payload（含 HTTP 反拉后的 score/risk）更新快照质检列。"""
    from common.db.session import async_session_factory

    payload = await _enrich_qc_payload_from_http(payload)
    session_id = str(payload.get("session_id") or "")
    qc_record_id = payload.get("qc_record_id")
    score = payload.get("score")
    risk = payload.get("risk")

    compliance_score: int | None = None
    if score is not None:
        try:
            compliance_score = int(score)
        except (ValueError, TypeError):
            compliance_score = None

    risk_level: str | None = str(risk) if risk is not None else None

    has_high = "0"
    if session_id:
        try:
            async with async_session_factory() as session:
                high = await check_has_high_alert(session, session_id)
                has_high = "1" if high else "0"
        except Exception:
            logger.exception("check_has_high_alert failed", extra={"session_id": session_id})

    try:
        async with async_session_factory() as session:
            updated = await update_qc_cols(
                session,
                case_id,
                qc_record_id=str(qc_record_id) if qc_record_id else None,
                compliance_score=compliance_score,
                risk_level=risk_level,
                has_high_alert=has_high,
            )
            if updated:
                await session.commit()
                logger.info(
                    "snapshot_qc_cols_updated",
                    extra={
                        "case_id": case_id,
                        "session_id": session_id,
                        "compliance_score": compliance_score,
                        "risk_level": risk_level,
                        "has_high_alert": has_high,
                    },
                )
            else:
                logger.warning(
                    "snapshot_qc_cols_not_found",
                    extra={"case_id": case_id},
                )
            return updated
    except Exception:
        logger.exception("update_snapshot_qc_cols failed", extra={"case_id": case_id})
        return False


async def _confirm_qc_record(session_id: str) -> bool:
    """确认 qc_records 已落库（真开检）。"""
    from common.db.session import async_session_factory

    if not session_id:
        return False
    try:
        async with async_session_factory() as session:
            return await qc_record_exists(session, session_id)
    except Exception:
        logger.exception("qc_record_exists check failed", extra={"session_id": session_id})
        return False


async def handle_qc_check_completed(payload: dict[str, Any]) -> dict[str, Any]:
    """收到 qc.check.completed：确认落库 → HTTP 反拉详情 → 仅回流快照质检列。

    SHALL NOT 调用投诉创建 / judge / review（投诉链路独立，由脚本或投诉服务触发）。
    """
    payload = await _enrich_qc_payload_from_http(payload)
    case_id = str(payload.get("case_id") or "")
    session_id = str(payload.get("session_id") or "")

    if not case_id:
        logger.warning("qc.check.completed missing case_id, skip")
        return {"status": "skipped", "reason": "missing_case_id"}

    logger.info(
        "qc.check.completed received",
        extra={
            "case_id": case_id,
            "session_id": session_id,
            "qc_record_id": payload.get("qc_record_id"),
            "score": payload.get("score"),
            "risk": payload.get("risk"),
        },
    )

    if not await _confirm_qc_record(session_id):
        logger.error(
            "qc_record_not_found_skip_snapshot",
            extra={"case_id": case_id, "session_id": session_id},
        )
        return {"status": "skipped", "reason": "qc_record_not_found", "case_id": case_id}

    updated = await _update_snapshot_qc_cols(case_id, payload)
    return {
        "status": "ok" if updated else "partial",
        "case_id": case_id,
        "snapshot_updated": updated,
    }


async def handle_complaint_result_ready(payload: dict[str, Any]) -> dict[str, Any]:
    """确认 complaint.result.ready：记日志；不替代 write_session 写投诉列。"""
    case_id = str(payload.get("case_id") or payload.get("complaint_no") or "")
    logger.info(
        "complaint.result.ready confirmed",
        extra={
            "event": "complaint.result.ready.confirmed",
            "case_id": case_id or None,
            "complaint_no": payload.get("complaint_no"),
            "trace_id": payload.get("trace_id"),
        },
    )
    return {"status": "ok", "case_id": case_id, "confirmed": True}


class QcCheckSubscriber:
    """订阅 qc.check.completed + complaint.result.ready（经 EventBus）。

    不订阅 qc.complaint.suspected；不自动建投诉；不轮询等待。
    """

    def __init__(
        self,
        *,
        handler=None,
        result_ready_handler=None,
        settings=dp_settings,
    ) -> None:
        self._handler = handler or handle_qc_check_completed
        self._result_ready_handler = result_ready_handler or handle_complaint_result_ready
        self._settings = settings
        self._seed_times: dict[str, float] = {}

    def track_seed(self, case_id: str, session_id: str) -> None:
        """记录种子入库时间，用于超时告警。"""
        self._seed_times[case_id] = time.time()
        logger.info(
            "seed_tracked_for_timeout",
            extra={"case_id": case_id, "session_id": session_id},
        )

    def check_timeouts(self) -> list[dict[str, Any]]:
        timeout_seconds = self._settings.QC_TIMEOUT_MINUTES * 60
        now = time.time()
        timed_out: list[dict[str, Any]] = []
        for case_id, seed_time in list(self._seed_times.items()):
            elapsed = now - seed_time
            if elapsed >= timeout_seconds:
                wait_minutes = int(elapsed / 60)
                logger.warning(
                    "qc_check_completed_timeout",
                    extra={
                        "case_id": case_id,
                        "wait_minutes": wait_minutes,
                        "timeout_minutes": self._settings.QC_TIMEOUT_MINUTES,
                    },
                )
                timed_out.append({
                    "case_id": case_id,
                    "wait_minutes": wait_minutes,
                })
                del self._seed_times[case_id]
        return timed_out

    def _clear_tracked(self, case_id: str) -> None:
        self._seed_times.pop(case_id, None)

    async def handle_envelope(self, envelope: EventEnvelope) -> Any:
        """EventBus handler：从信封 ``data`` 提取字段后接入处理逻辑。"""
        name = envelope.event_name
        if name == CHANNEL_QC_COMPLAINT_SUSPECTED:
            logger.info("qc.complaint.suspected ignored (not for auto-create)")
            return {"status": "ignored", "channel": name}

        payload = dict(envelope.data)
        if envelope.trace_id and "trace_id" not in payload:
            payload["trace_id"] = envelope.trace_id

        if name == CHANNEL_COMPLAINT_RESULT_READY:
            try:
                return await self._result_ready_handler(payload)
            except Exception:
                logger.exception("complaint.result.ready handler failed")
                raise

        if name != CHANNEL_QC_CHECK_COMPLETED:
            logger.info("unsubscribed channel ignored: %s", name)
            return {"status": "ignored", "channel": name}

        case_id = str(payload.get("case_id") or "")
        if case_id:
            self._clear_tracked(case_id)
        try:
            return await self._handler(payload)
        except Exception:
            logger.exception("qc.check.completed handler failed")
            raise

    async def handle_message(self, channel: str, raw: Any) -> Any:
        """兼容单测：channel + raw；信封则转 handle_envelope。"""
        name = channel.decode("utf-8") if isinstance(channel, (bytes, bytearray)) else str(channel)

        if name == CHANNEL_QC_COMPLAINT_SUSPECTED:
            logger.info("qc.complaint.suspected ignored (not for auto-create)")
            return {"status": "ignored", "channel": name}

        if name != CHANNEL_QC_CHECK_COMPLETED:
            logger.info("unsubscribed channel ignored: %s", name)
            return {"status": "ignored", "channel": name}

        payload = parse_event_payload(raw)
        if isinstance(payload, dict) and "event_id" in payload and "data" in payload:
            try:
                from common.eventbus.envelope import parse_envelope

                return await self.handle_envelope(
                    parse_envelope(payload, fallback_event_name=name)
                )
            except Exception:
                logger.exception("qc.check.completed envelope parse failed")
                return {"status": "error", "channel": name}

        case_id = str(payload.get("case_id") or "")
        if case_id:
            self._clear_tracked(case_id)

        try:
            return await self._handler(payload)
        except Exception:
            logger.exception("qc.check.completed handler failed")
            return {"status": "error", "case_id": case_id}

    async def listen(self, redis: Any | None = None) -> None:
        """订阅 qc.check.completed + complaint.result.ready（经 EventBus）。"""
        import os

        from common.eventbus.factory import get_event_bus

        if not (os.getenv("EVENT_BUS") or "").strip():
            os.environ["EVENT_BUS"] = "rocketmq"
        if not (os.getenv("EVENT_BUS_SVC") or "").strip():
            os.environ["EVENT_BUS_SVC"] = "dp"

        # ``redis`` arg kept for API compatibility; bus transport is RocketMQ only.
        bus = get_event_bus()

        async def _qc_handler(envelope: EventEnvelope) -> None:
            await self.handle_envelope(envelope)
            self.check_timeouts()

        async def _result_ready_handler(envelope: EventEnvelope) -> None:
            if envelope.event_name != CHANNEL_COMPLAINT_RESULT_READY:
                envelope = envelope.model_copy(
                    update={"event_name": CHANNEL_COMPLAINT_RESULT_READY}
                )
            await self.handle_envelope(envelope)

        await bus.subscribe(CHANNEL_QC_CHECK_COMPLETED, "dp", _qc_handler)
        # 消费组短名 dp → gid_dp_complaint_result_ready
        await bus.subscribe(CHANNEL_COMPLAINT_RESULT_READY, "dp", _result_ready_handler)
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            close = getattr(bus, "aclose", None)
            if close is not None:
                await close()
            raise

    async def listen_forever(self, redis: Any | None = None) -> None:
        while True:
            try:
                await self.listen(redis)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("subscribe loop failed; retry")
                await asyncio.sleep(1.0)

    async def watchdog_loop(self) -> None:
        """独立定时看门狗：只按本地时间表检查超时。"""
        while True:
            try:
                await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
                self.check_timeouts()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("timeout watchdog loop failed; continue")


_subscriber_instance: "QcCheckSubscriber | None" = None


def get_subscriber() -> QcCheckSubscriber:
    global _subscriber_instance
    if _subscriber_instance is None:
        _subscriber_instance = QcCheckSubscriber()
    return _subscriber_instance


def skip_background_tasks() -> bool:
    import os

    if os.environ.get("DP_SKIP_BACKGROUND") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


async def start_background_tasks() -> list[asyncio.Task[Any]]:
    """启动 qc.check.completed 订阅 + 超时看门狗。"""
    if skip_background_tasks():
        return []
    tasks: list[asyncio.Task[Any]] = []
    try:
        redis = await get_redis()
        subscriber = get_subscriber()

        async def _listen() -> None:
            try:
                await subscriber.listen_forever(redis)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("subscribe background task died")

        tasks.append(asyncio.create_task(_listen(), name="dp-qc-subscribe"))
        tasks.append(
            asyncio.create_task(subscriber.watchdog_loop(), name="dp-qc-timeout-watchdog")
        )
    except Exception:
        logger.exception("dp background start failed")
    return tasks
