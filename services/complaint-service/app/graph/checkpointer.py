"""LangGraph checkpointer 工厂与进程级生命周期。

生产：AsyncPostgresSaver + psycopg AsyncConnectionPool（禁止裸连接）。
本地/测试：MemorySaver（未配置 DSN 时的默认，行为与接入前完全一致）。

设计要点：
- **schema 归 alembic 所有**：本模块不调 `saver.setup()`，改为启动时做一次
  schema 探针（`_probe_schema`）。探针失败 → 降级 MemorySaver，服务照常启动。
  若不做探针，未跑迁移的环境会在图执行内部才炸，异常被 `_judge_with_graph`
  的 except 吞成 unknown_error，极难定位。
- **DSN 未配置 ≠ 降级**：dev/test 走 MemorySaver 是预期行为，`is_checkpointer_degraded()`
  返回 False。只有「期望 PG 但退回 MemorySaver」才算降级。
- **saver 绑定 event loop**：`AsyncPostgresSaver.__init__` 会 `asyncio.get_running_loop()`，
  因此实例必须在 lifespan 的 loop 内创建；测试用 `reset_checkpointer_for_tests()` 清理。
"""
from __future__ import annotations

import os
from typing import Optional

import structlog

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = structlog.get_logger()

# ── 进程级持有 ──
_SAVER: Optional[BaseCheckpointSaver] = None
_POOL: Optional[object] = None          # AsyncConnectionPool，供 shutdown 关闭
_DEGRADED: bool = False                 # 期望 PG 但退回了 MemorySaver
_INIT_DONE: bool = False                # 防止 PG 挂掉后每个请求都重试建池


def get_pg_dsn() -> Optional[str]:
    """解析 checkpointer 用的 PG DSN。

    优先级：显式 COMPLAINT_PG_DSN（env > settings）> 开关开启时由 PG_* 派生 > None。
    保留 os.getenv 优先以兼容存量测试（它们直接设 os.environ['COMPLAINT_PG_DSN']）。
    """
    explicit = os.getenv("COMPLAINT_PG_DSN", "").strip()
    if explicit:
        return explicit

    try:
        from app.config import settings
    except Exception:  # noqa: BLE001 — 无 settings 上下文时退化为「未配置」
        return None

    configured = str(getattr(settings, "COMPLAINT_PG_DSN", "") or "").strip()
    if configured:
        return configured

    if getattr(settings, "COMPLAINT_CHECKPOINTER_PG", False):
        return settings.pg_dsn_psycopg3

    return None


def _pool_bounds() -> tuple[int, int]:
    """连接池上下界（配置驱动，生产可调小以避免打满 PG max_connections）。"""
    try:
        from app.config import settings

        return (
            int(getattr(settings, "COMPLAINT_PG_POOL_MIN", 5)),
            int(getattr(settings, "COMPLAINT_PG_POOL_MAX", 20)),
        )
    except Exception:  # noqa: BLE001
        return (5, 20)


async def _probe_schema(pool) -> None:
    """探针：确认 alembic 0015 已建出 checkpoint 三表。

    缺失即抛，由 get_checkpointer 的 except 收敛为 MemorySaver 降级。
    """
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT to_regclass('public.checkpoints') AS t")
        row = await cur.fetchone()
    value = row["t"] if isinstance(row, dict) else (row[0] if row else None)
    if value is None:
        raise RuntimeError(
            "checkpoint schema missing — run `alembic upgrade head` "
            "(expects 0015_langgraph_checkpoint_tables)"
        )


async def get_checkpointer(force_memory: bool = False) -> BaseCheckpointSaver:
    """构造 checkpointer（纯工厂；持有与生命周期见 init_checkpointer）。

    - 未配置 DSN 或 force_memory → MemorySaver
    - 配置了 DSN → AsyncPostgresSaver(psycopg AsyncConnectionPool) + schema 探针
    - 建池/探针失败 → 关池 + MemorySaver 降级
    """
    global _POOL

    dsn = get_pg_dsn()
    if force_memory or not dsn:
        logger.info("checkpointer_memory_saver", reason="no_dsn_or_force")
        return MemorySaver()

    pool = None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        min_size, max_size = _pool_bounds()
        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            open=False,
        )
        await pool.open()
        await _probe_schema(pool)

        saver = AsyncPostgresSaver(pool)
        _POOL = pool
        logger.info("checkpointer_postgres_ready", pool_min=min_size, pool_max=max_size)
        return saver
    except Exception as e:  # noqa: BLE001 — 降级不阻断启动
        logger.warning("checkpointer_pg_failed_fallback", error=str(e))
        if pool is not None:
            try:
                await pool.close()
            except Exception:  # noqa: BLE001 — 关池失败不掩盖主因
                logger.warning("checkpointer_pool_close_failed")
        return MemorySaver()


async def init_checkpointer(force_memory: bool = False) -> BaseCheckpointSaver:
    """lifespan 调用。幂等：_INIT_DONE 后直接返回已建实例，不再重试失败过的 PG。"""
    global _SAVER, _DEGRADED, _INIT_DONE

    if _INIT_DONE and _SAVER is not None:
        return _SAVER

    expected_pg = (not force_memory) and get_pg_dsn() is not None
    saver = await get_checkpointer(force_memory=force_memory)

    _SAVER = saver
    _DEGRADED = expected_pg and isinstance(saver, MemorySaver)
    _INIT_DONE = True

    if _DEGRADED:
        logger.warning("checkpointer_degraded_to_memory", reason="pg_unavailable")
    return _SAVER


async def get_cached_checkpointer() -> Optional[BaseCheckpointSaver]:
    """已初始化的实例；未初始化返回 None（调用方决定是否 init）。"""
    return _SAVER


def checkpointer_kind() -> str:
    """'postgres' | 'memory' —— 供 /health 暴露，区分「真持久化」与「断点会丢」。"""
    if _SAVER is None:
        return "memory"
    return "memory" if isinstance(_SAVER, MemorySaver) else "postgres"


def is_checkpointer_degraded() -> bool:
    """True 当且仅当「期望 PG 但退回了 MemorySaver」。未配置 DSN 不算降级。"""
    return _DEGRADED


async def shutdown_checkpointer() -> None:
    """lifespan shutdown 调用。关闭连接池；幂等，重复调用不抛。"""
    global _POOL, _SAVER, _DEGRADED, _INIT_DONE

    pool, _POOL = _POOL, None
    if pool is not None:
        try:
            await pool.close()
        except Exception:  # noqa: BLE001 — loop 可能已关闭，不阻断 shutdown
            logger.warning("checkpointer_pool_close_failed_on_shutdown")

    _SAVER = None
    _DEGRADED = False
    _INIT_DONE = False


def reset_checkpointer_for_tests() -> None:
    """测试清理入口：丢弃进程级实例与池引用。

    同步实现（不 await pool.close()）：池可能绑定在已关闭的 loop 上，
    此时 close() 会抛；测试隔离靠丢弃引用 + 唯一 id，不依赖优雅关池。
    """
    global _POOL, _SAVER, _DEGRADED, _INIT_DONE

    _POOL = None
    _SAVER = None
    _DEGRADED = False
    _INIT_DONE = False


# ── 归档 / 挂起钩子（本批仅声明签名，不实现调度）──────────────

# ── 三表无界增长治理 ──────────────────────────────────────

# 可安全裁剪的终态：这些状态不再需要 resume，其 checkpoint 无保留价值。
# **严禁**把 pending_review / pending_senior_review 放进来——它们正在等人工
# 复核，删 checkpoint 就等于丢掉 resume 能力（本功能存在的全部意义）。
_PRUNABLE_STATUSES = ("resolved", "closed", "archived")

_PRUNE_CANDIDATES_SQL = f"""
SELECT cs.complaint_id::text AS thread_id
FROM complaint_sessions cs
WHERE cs.task_status IN {_PRUNABLE_STATUSES!r}
  AND cs.is_delete = '0'
  AND cs.update_time < now() - (:days * interval '1 day')
  AND EXISTS (SELECT 1 FROM checkpoints c WHERE c.thread_id = cs.complaint_id::text)
"""


async def prune_checkpoints(retention_days: int = 90, *, dry_run: bool = True) -> dict:
    """裁剪终态线程的 checkpoint 三表，治理无界增长。

    **整线程删除**而非「保留最后 N 个」：`checkpoint_blobs` 按
    `(thread_id, channel, version)` 存储，与 `checkpoint_id` 无外键关联，
    按 checkpoint 条数保留会留下无法引用的孤儿 blob，反而越积越多。
    终态线程不需要任何 checkpoint，整线程删除干净且安全。

    安全不变量：只处理 `task_status ∈ _PRUNABLE_STATUSES` 且 `update_time`
    早于 `retention_days` 的线程。活跃/待人工复核的线程一个都不会碰。

    dry_run 默认 True：先看会删什么，确认后再传 False。返回统计字典。
    """
    result = {"dry_run": dry_run, "threads": [], "deleted": {}}
    if retention_days <= 0:
        logger.warning("prune_checkpoints_skipped", reason="non_positive_retention")
        return result

    try:
        from sqlalchemy import bindparam, text

        from common.db.session import async_session_factory

        async with async_session_factory() as db:
            cur = await db.execute(text(_PRUNE_CANDIDATES_SQL), {"days": retention_days})
            threads = [str(r[0]) for r in cur.fetchall()]
            result["threads"] = threads
            if not threads or dry_run:
                return result

            deleted = {}
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                stmt = text(f"DELETE FROM {table} WHERE thread_id IN :tids").bindparams(
                    bindparam("tids", expanding=True)
                )
                res = await db.execute(stmt, {"tids": threads})
                deleted[table] = int(res.rowcount or 0)
            await db.commit()
            result["deleted"] = deleted
            logger.info("prune_checkpoints_done",
                        threads=len(threads), retention_days=retention_days, **deleted)
            return result
    except Exception as e:  # noqa: BLE001 — 维护任务失败不影响服务
        # 保留 fail-open（维护任务不应拖垮服务），但记下异常类型：
        # 曾经因为只记 error 字符串，SQLAlchemy 的 ProgrammingError 被当成
        # 普通连库失败，导致 prune 长期整体失效却无人察觉。
        logger.warning("prune_checkpoints_failed",
                       error=str(e), error_type=type(e).__name__)
        return result


async def archive_expired_sessions(older_than_days: int = 90) -> int:
    """归档超过 N 天的终态会话，返回被清理的线程数。

    定时调度（apscheduler）本批仍未接入；当前需人工/外部 cron 调用。
    实际动作委托 `prune_checkpoints(dry_run=False)`。
    """
    stats = await prune_checkpoints(retention_days=older_than_days, dry_run=False)
    return len(stats.get("threads") or [])


async def handle_suspended_sessions(suspend_hours: int = 24) -> int:
    """处理挂起超过 N 小时的会话（转人工）。本批 stub：返回 0。"""
    # TODO(batch7+): 实现 24h 挂起转人工策略
    return 0
