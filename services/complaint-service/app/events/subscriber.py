"""complaint-service EventBus 入站订阅（4.4 注册 + 5.2 业务处理）。

``complaint.created``：双层幂等（event_id → case_id）后组装正文 → create → judge(run_graph=true)。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from common.eventbus.envelope import EventEnvelope

logger = structlog.get_logger()

CHANNEL_COMPLAINT_CREATED = "complaint.created"
SUBSCRIBE_CHANNELS: tuple[str, ...] = (CHANNEL_COMPLAINT_CREATED,)
_GROUP_SHORT = "cs"  # → gid_cs_complaint_created

# 进程内 event_id 去重（单测 / mock）；生产另有 Redis SET NX
_seen_event_ids: set[str] = set()
_EVENT_ID_TTL_KEY = "cs:event_id:complaint_created:{event_id}"


def skip_eventbus_subscriber() -> bool:
    """单测 / 显式跳过时不连 RocketMQ，避免阻塞 TestClient lifespan。"""
    if os.environ.get("CS_SKIP_EVENTBUS") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def reset_event_id_dedupe() -> None:
    """测试用：清空进程内 event_id 集合。"""
    _seen_event_ids.clear()


async def _claim_event_id(event_id: str) -> bool:
    """True = 首次见到，应处理；False = 重复投递，跳过。"""
    if not event_id:
        return True
    if event_id in _seen_event_ids:
        return False
    try:
        from common.resilience._redis import get_redis

        redis = get_redis()
        key = _EVENT_ID_TTL_KEY.format(event_id=event_id)
        ok = await redis.set(key, "1", nx=True, ex=86400)
        if ok is None or ok is False:
            _seen_event_ids.add(event_id)
            return False
    except Exception:  # noqa: BLE001 — Redis 不可用时退化为进程内去重
        pass
    _seen_event_ids.add(event_id)
    return True


def _status_value(status: Any) -> str:
    if status is None:
        return ""
    return status.value if hasattr(status, "value") else str(status)


# 已越过「仅建单」：不再因重复 complaint.created 二次完整跑图
_SKIP_JUDGE_STATUSES = frozenset(
    {
        "pending_review",
        "pending_senior_review",
        "pending_callback",
        "judging",
        "draft_generated",
        "evidence_gathering",
        "reflection_retry",
        "resolved",
        "closed",
        "archived",
    }
)


async def handle_complaint_created(envelope: EventEnvelope) -> None:
    """消费 complaint.created：组装正文 → 幂等 create → judge(run_graph=true)。"""
    data = envelope.data if isinstance(envelope.data, dict) else {}
    case_id = str(data.get("case_id") or "").strip()
    event_id = str(envelope.event_id or "")
    logger.info(
        "complaint_created_received",
        event_id=event_id,
        case_id=case_id or None,
        trace_id=envelope.trace_id,
    )
    if not case_id:
        logger.warning(
            "complaint_created_missing_case_id",
            event_id=event_id,
            trace_id=envelope.trace_id,
        )
        return

    if not await _claim_event_id(event_id):
        logger.info(
            "complaint_created_event_id_deduped",
            event_id=event_id,
            case_id=case_id,
        )
        return

    from app.service.complaint_service import complaint_service
    from app.service.complaint_text_builder import assemble_complaint_text_for_case

    # 业务键幂等：complaint_id == case_id；已研判/在途则不再完整跑图
    existing = complaint_service.get(case_id)
    if existing is not None:
        status_val = _status_value(existing.get("task_status"))
        if status_val in _SKIP_JUDGE_STATUSES:
            logger.info(
                "complaint_created_case_idempotent_skip",
                event_id=event_id,
                case_id=case_id,
                task_status=status_val,
            )
            return
        complaint_id = str(existing.get("complaint_id") or case_id)
        category = "existing"
    else:
        text_body, category, _session_id = await assemble_complaint_text_for_case(
            case_id
        )
        rec = await complaint_service.create_async(
            text_body, case_id=case_id, session_id=None
        )
        complaint_id = str(rec.get("complaint_id") or case_id)
        logger.info(
            "complaint_created_persisted",
            event_id=event_id,
            case_id=case_id,
            complaint_id=complaint_id,
            category=category,
        )

    # 事件入口必经 judge；停在 human_review 时尚未 write_session
    await complaint_service.judge_async(complaint_id, run_graph=True)
    logger.info(
        "complaint_created_judged",
        event_id=event_id,
        case_id=case_id,
        complaint_id=complaint_id,
        category=category,
    )


async def _dispatch(envelope: EventEnvelope) -> None:
    if envelope.event_name == CHANNEL_COMPLAINT_CREATED:
        await handle_complaint_created(envelope)
        return
    logger.warning(
        "complaint_eventbus_unhandled",
        event_name=envelope.event_name,
        event_id=envelope.event_id,
    )


class ComplaintEventSubscriber:
    """经 EventBus 订阅业务频道；listen 失败由 listen_forever 重试。"""

    def __init__(self) -> None:
        self._bus: Any | None = None

    async def listen(self) -> None:
        from common.eventbus.factory import get_event_bus

        if not (os.getenv("EVENT_BUS") or "").strip():
            os.environ["EVENT_BUS"] = "rocketmq"
        if not (os.getenv("EVENT_BUS_SVC") or "").strip():
            os.environ["EVENT_BUS_SVC"] = _GROUP_SHORT

        bus = get_event_bus()
        self._bus = bus

        for channel in SUBSCRIBE_CHANNELS:

            async def _handler(
                envelope: EventEnvelope, *, _name: str = channel
            ) -> None:
                if envelope.event_name != _name:
                    envelope = envelope.model_copy(update={"event_name": _name})
                await _dispatch(envelope)

            await bus.subscribe(channel, _GROUP_SHORT, _handler)

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            close = getattr(bus, "aclose", None)
            if close is not None:
                await close()
            logger.info(
                "complaint_eventbus_subscriber_stopped",
                channels=list(SUBSCRIBE_CHANNELS),
                group=_GROUP_SHORT,
                reason="cancelled",
            )
            raise

    async def listen_forever(self) -> None:
        while True:
            try:
                await self.listen()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("complaint eventbus subscribe loop failed; retry")
                await asyncio.sleep(1.0)


async def start_eventbus_subscriber() -> asyncio.Task[Any] | None:
    """启动订阅后台任务。失败只记日志，不抛给 FastAPI lifespan。"""
    if skip_eventbus_subscriber():
        logger.info("complaint_eventbus_subscriber_skipped")
        return None
    try:
        subscriber = ComplaintEventSubscriber()

        async def _run() -> None:
            try:
                await subscriber.listen_forever()
            except asyncio.CancelledError:
                if subscriber._bus is None:
                    logger.info(
                        "complaint_eventbus_subscriber_stopped",
                        channels=list(SUBSCRIBE_CHANNELS),
                        group=_GROUP_SHORT,
                        reason="task_cancelled_before_listen",
                    )
                raise
            except Exception:
                logger.exception("complaint eventbus subscriber task died")

        return asyncio.create_task(_run(), name="cs-eventbus-subscribe")
    except Exception:
        logger.exception("complaint eventbus subscriber start failed")
        return None
