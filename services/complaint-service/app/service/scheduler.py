"""定时任务调度（9.4）。

apscheduler stub（本批可调用 job 函数，不真起调度）：
- 每日：清理 90 天归档会话、挂起积压清理、Redis DB6 RDB 快照
- 每周：archive 表全量备份至对象存储（stub）
- 每季度：备份恢复演练（stub）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from common.utils.time_parse import parse_epoch_or_datetime, parse_naive_or_aware


def cleanup_archived_90d(
    session_store: dict | None = None, *, clock: Any = None
) -> int:
    """清理 90 天前已归档会话。返回清理数。

    T52：时间域统一（time_parse 单源）——``updated_at`` 的 aware/naive
    datetime、ISO 串、float epoch 旧数据一律收敛 aware 后与 90 天窗比较；
    缺 ``updated_at`` 的 archived 记录**保守跳过**（MUST NOT 以默认 0 参与
    比较——``rec.get("updated_at", 0)`` 的语义反转会把无时间戳记录立即误删）。
    ``clock`` 可注入（默认 aware UTC now；float epoch 注入归一，测试钉边界用）。
    """
    if session_store is None:
        return 0
    if clock is None:
        now = datetime.now(timezone.utc)
    else:
        now = clock()
        if isinstance(now, (int, float)):
            now = datetime.fromtimestamp(float(now), tz=timezone.utc)
        else:
            now = parse_naive_or_aware(now)
    cutoff = now - timedelta(days=90)
    removed = 0
    for sid in list(session_store.keys()):
        rec = session_store[sid]
        if rec.get("task_status") != "archived":
            continue
        updated = rec.get("updated_at")
        if updated is None:
            # 保守跳过：无时间戳不删（T52 语义反转修复，spec §4.5）
            continue
        try:
            updated_dt = parse_epoch_or_datetime(updated)
        except ValueError:
            continue
        if updated_dt is None or updated_dt >= cutoff:
            # >= cutoff（含 90 天整）：窗内不删（严格小于才清理，与原语义一致）
            continue
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
