"""etl.task.done / call.session.ended 发布器（data-platform → qc-service）。

本 change 平移的 **QC 链路三事件** 中，DP 负责发布其中两条：
``call.session.ended``、``etl.task.done``（经统一 EventBus）。
``qc.check.completed`` 由 qc-service 发布（见 qc publisher ``_BUS_CHANNELS``）。

经统一 EventBus 发布；``EVENT_BUS=redis`` 时落 QC_REDIS_DB（默认 3）。
业务 payload 字段构造保持不变；上总线时仅将白名单定位字段写入信封 ``data``。
禁止 HTTP POST 调用 qc-service。
禁止发布到 0 号库。

范围说明：投诉链事件（``complaint.created`` 等）与 qc 其余 M1 频道
不在本 change；勿误判为「漏迁」。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import dp_settings

logger = logging.getLogger(__name__)

CHANNEL_ETL_TASK_DONE = "etl.task.done"
CHANNEL_SESSION_ENDED = "call.session.ended"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _ensure_event_bus_env() -> None:
    """Default grayscale transport when unset (local tests / scripts)."""
    if not (os.getenv("EVENT_BUS") or "").strip():
        os.environ["EVENT_BUS"] = "redis"
    if not (os.getenv("EVENT_BUS_SVC") or "").strip():
        os.environ["EVENT_BUS_SVC"] = "dp"


async def _bus_publish(
    event_name: str,
    payload: dict[str, Any],
    *,
    key: str | None,
) -> None:
    from common.eventbus.factory import get_event_bus
    from common.eventbus.util import whitelist_data

    _ensure_event_bus_env()
    bus = get_event_bus()
    await bus.publish(
        event_name,
        whitelist_data(payload),
        key=key,
        trace_id=str(payload.get("trace_id") or "") or None,
    )


async def publish_etl_task_done(
    *,
    case_id: str,
    session_id: str,
    backfill_date: datetime | None = None,
) -> bool:
    """发布 etl.task.done（经 EventBus）。

    Args:
        case_id: 种子案件 UUID（= public.cases.id）
        session_id: 种子通话 session_id
        backfill_date: 通话结束那天（Asia/Shanghai）；None 时取当天

    Returns:
        True 表示发布成功，False 表示失败（失败也留下可查日志记录）。
    """
    if backfill_date is None:
        backfill_date = datetime.now(_SHANGHAI)
    # backfill_date 取日期部分（YYYY-MM-DD），对齐 qc-service pending-qc 查询
    backfill_date_str = backfill_date.strftime("%Y-%m-%d")
    publish_time = datetime.now(_SHANGHAI).isoformat()

    payload: dict[str, Any] = {
        "case_id": case_id,
        "session_id": session_id,
        "backfill_date": backfill_date_str,
        "trace_id": f"etl-{case_id}",
        "ts": int(time.time()),
    }

    try:
        await _bus_publish(CHANNEL_ETL_TASK_DONE, payload, key=session_id)
        logger.info(
            "etl.task.done published",
            extra={
                "event": "etl.task.done.published",
                "case_id": case_id,
                "session_id": session_id,
                "backfill_date": backfill_date_str,
                "publish_time": publish_time,
                "channel": CHANNEL_ETL_TASK_DONE,
                "redis_db": dp_settings.QC_REDIS_DB,
                "event_bus": os.getenv("EVENT_BUS", "redis"),
            },
        )
        return True
    except Exception as exc:
        # 发布失败：留下可查记录，不假装成功
        logger.error(
            "etl.task.done publish FAILED",
            extra={
                "event": "etl.task.done.publish_failed",
                "case_id": case_id,
                "session_id": session_id,
                "backfill_date": backfill_date_str,
                "publish_time": publish_time,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        return False


async def publish_call_session_ended(*, session_id: str) -> bool:
    """发布 call.session.ended（经 EventBus）；业务/线上 data 仅含 session_id。"""
    publish_time = datetime.now(_SHANGHAI).isoformat()
    payload: dict[str, Any] = {
        "session_id": session_id,
    }
    try:
        await _bus_publish(CHANNEL_SESSION_ENDED, payload, key=session_id)
        logger.info(
            "call.session.ended published",
            extra={
                "event": "call.session.ended.published",
                "session_id": session_id,
                "publish_time": publish_time,
                "channel": CHANNEL_SESSION_ENDED,
                "event_bus": os.getenv("EVENT_BUS", "redis"),
            },
        )
        return True
    except Exception as exc:
        logger.error(
            "call.session.ended publish FAILED",
            extra={
                "event": "call.session.ended.publish_failed",
                "session_id": session_id,
                "publish_time": publish_time,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        return False
