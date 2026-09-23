"""etl.task.done 发布器（data-platform → qc-service）。

发布到 QC_REDIS_DB（默认 3），与 qc-service 已有订阅一致。
payload 含 backfill_date 等于种子通话结束那天（Asia/Shanghai）。
禁止 HTTP POST 调用 qc-service，仅通过 Redis 事件。
禁止发布到 0 号库。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import dp_settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

CHANNEL_ETL_TASK_DONE = "etl.task.done"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


async def publish_etl_task_done(
    *,
    case_id: str,
    session_id: str,
    backfill_date: datetime | None = None,
) -> bool:
    """发布 etl.task.done 到 QC_REDIS_DB。

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
        redis = await get_redis()
        # 库号已由 build_redis() 设为 QC_REDIS_DB（默认 3），不进 0 号库
        channel = CHANNEL_ETL_TASK_DONE
        message = json.dumps(payload, ensure_ascii=False)
        await redis.publish(channel, message)
        logger.info(
            "etl.task.done published",
            extra={
                "event": "etl.task.done.published",
                "case_id": case_id,
                "session_id": session_id,
                "backfill_date": backfill_date_str,
                "publish_time": publish_time,
                "channel": channel,
                "redis_db": dp_settings.QC_REDIS_DB,
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
