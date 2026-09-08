"""每日对账定时入口（8.1 + 8.4 共用，避免两套定时）。

- 扫描 draft_generated 超 1h 未确认工单 → 触发回调补偿
- 扫描 judging/draft_generated 超 30min → pending_review
本批用同步函数，Mock 时钟可测；真实调度由批次9+ 接入。
"""
from __future__ import annotations

from typing import Any

from app.service.callback_service import CallbackService
from app.service.session_service import SessionService


def daily_reconcile(
    session_service: SessionService,
    callback_service: CallbackService,
    *,
    stale_hours: float = 1.0,
) -> dict[str, Any]:
    """执行每日对账，返回处理统计。"""
    # 1. 超时转 pending_review
    timed_out = session_service.timeout_sessions()

    # 2. draft_generated 超 1h → 回调补偿
    stale = session_service.list_stale_draft_generated(stale_hours=stale_hours)
    compensated: list[str] = []
    for sid in stale:
        try:
            callback_service.send(sid, version=1, payload={"reason": "reconcile_retry"})
            compensated.append(sid)
        except Exception:
            pass

    return {
        "timed_out": timed_out,
        "stale_draft_generated": stale,
        "compensated": compensated,
    }
