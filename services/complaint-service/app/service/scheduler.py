"""定时任务调度（9.4）。

apscheduler stub（本批可调用 job 函数，不真起调度）：
- 每日：清理 90 天归档会话、挂起积压清理、Redis DB6 RDB 快照
- 每周：archive 表全量备份至对象存储（stub）
- 每季度：备份恢复演练（stub）
"""
from __future__ import annotations

import time
from typing import Any


def cleanup_archived_90d(session_store: dict | None = None) -> int:
    """清理 90 天前已归档会话。返回清理数。"""
    if session_store is None:
        return 0
    cutoff = time.time() - 90 * 86400
    removed = 0
    for sid in list(session_store.keys()):
        rec = session_store[sid]
        if rec.get("task_status") == "archived" and rec.get("updated_at", 0) < cutoff:
            del session_store[sid]
            removed += 1
    return removed


def cleanup_stale_24h(recovery_service: Any | None = None,
                      session_store: dict | None = None) -> list[str]:
    """挂起积压清理（委托 recovery_service）。"""
    if recovery_service is not None and session_store is not None:
        return recovery_service.cleanup_stale(session_store)
    return []


def redis_rdb_snapshot() -> bool:
    """Redis DB6 RDB 快照（stub）。"""
    return True


def weekly_backup() -> bool:
    """archive 表全量备份至对象存储（stub，批次10+ 接真对象存储）。"""
    return True


def quarterly_restore_drill() -> bool:
    """备份恢复演练（stub）。"""
    return True


# apscheduler 注册桩（本批不真启动）
def register_jobs(scheduler: Any = None) -> None:
    """注册定时任务。scheduler 为 None 时仅声明 job 函数。"""
    if scheduler is None:
        return
    # 批次10+ 接入 apscheduler 后取消注释
    # scheduler.add_job(cleanup_archived_90d, "cron", hour=2)
    # scheduler.add_job(cleanup_stale_24h, "cron", hour=3)
    # scheduler.add_job(redis_rdb_snapshot, "cron", hour=4)
    # scheduler.add_job(weekly_backup, "cron", day_of_week="sun", hour=2)
    # scheduler.add_job(quarterly_restore_drill, "cron", month="1,4,7,10", day=1)
    pass
