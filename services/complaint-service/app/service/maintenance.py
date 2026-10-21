"""周期维护任务入口（B1 收口）。

由 lifespan 里的维护循环（见 app/main.py `_maintenance_loop`）周期调用。
每轮做两件**幂等**、且都从 PG 真相源出发的事：

1. **超时转人工**（`atimeout_sessions_stale`）：进程崩溃/重启后，滞留的
   `judging` / `draft_generated` 工单因内存态丢失而卡死，从 PG 查超期者转
   `pending_review` —— 这是「B1 走路线二」的落地（不自动恢复图，转人工重判）。
2. **三表裁剪**（`prune_checkpoints`）：治理 LangGraph checkpoint 无界增长。

刻意**不接**回调补偿（reconcile 的 `list_stale_draft_generated` → callback）：
其阈值与超时转人工自相矛盾（draft_generated 30min 已被转 pending_review，
到不了 1h 的回调补偿阈值），且依赖内存 `_sent` 幂等，属未接线 DEBT。

多副本防双跑：维护锁（`acquire_maintenance_lock`）保证同刻只有一个副本执行。
锁 fail-open —— 维护任务幂等，Redis 挂时各副本仍跑，重复执行只是空操作。
"""
from __future__ import annotations

import structlog

from app.service.graph_lock import (
    LOCK_ACQUIRED,
    LOCK_HELD,
    acquire_maintenance_lock,
    new_token,
    release_maintenance_lock,
)
from app.service.session_service import session_service

logger = structlog.get_logger()

# 维护锁 TTL：略大于一轮维护的耗时（秒级）。
_MAINTENANCE_LOCK_TTL = 60


async def run_maintenance() -> dict:
    """执行一轮维护，返回统计。幂等，可被多副本安全地并发调用。"""
    stats = {"timed_out": [], "pruned_threads": 0, "skipped": False}

    token = new_token()
    state = await acquire_maintenance_lock(_MAINTENANCE_LOCK_TTL, token=token)
    if state == LOCK_HELD:
        stats["skipped"] = True
        logger.debug("maintenance_skip_lock_held")
        return stats

    try:
        # 1. 超时转人工（PG 真相源）
        stats["timed_out"] = await session_service.atimeout_sessions_stale()

        # 2. checkpoint 三表裁剪
        from app.graph.checkpointer import prune_checkpoints

        pruned = await prune_checkpoints(retention_days=90, dry_run=False)
        stats["pruned_threads"] = len(pruned.get("threads") or [])
    finally:
        if state == LOCK_ACQUIRED:
            await release_maintenance_lock(token)

    logger.info("maintenance_run", timed_out=len(stats["timed_out"]),
                pruned_threads=stats["pruned_threads"])
    return stats
