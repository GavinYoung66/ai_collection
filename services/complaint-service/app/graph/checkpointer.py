"""LangGraph checkpointer 工厂。

生产：AsyncPostgresSaver + psycopg AsyncConnectionPool（min_size=5, max_size=20，禁止裸连接）。
本地/测试：MemorySaver stub。
PG 故障降级 Redis fallback 接口（本批 stub，不接真 Redis）。
90 天归档 / 24h 挂起转人工：仅声明钩子，不实现调度。
"""
from __future__ import annotations

import os
from typing import Optional

import structlog

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = structlog.get_logger()

DEFAULT_POOL_MIN = 5
DEFAULT_POOL_MAX = 20


def get_pg_dsn() -> Optional[str]:
    """从环境变量读取 PG DSN（不硬编码）。"""
    return os.getenv("COMPLAINT_PG_DSN")


async def get_checkpointer(force_memory: bool = False) -> BaseCheckpointSaver:
    """获取 checkpointer。

    - 本地/测试（无 DSN 或 force_memory）→ MemorySaver
    - 生产 → AsyncPostgresSaver(psycopg AsyncConnectionPool, min=5/max=20)
    - PG / 依赖不可用 → Redis fallback stub（本批降级为 MemorySaver）
    """
    dsn = get_pg_dsn()
    if force_memory or not dsn:
        logger.info("checkpointer_memory_saver", reason="no_dsn_or_force")
        return MemorySaver()

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        # 官方推荐：psycopg AsyncConnectionPool，再交给 AsyncPostgresSaver
        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=DEFAULT_POOL_MIN,
            max_size=DEFAULT_POOL_MAX,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            open=False,
        )
        await pool.open()
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        return saver
    except Exception as e:  # noqa: BLE001
        logger.warning("checkpointer_pg_failed_fallback", error=str(e))
        return _redis_fallback_saver()


def _redis_fallback_saver() -> BaseCheckpointSaver:
    """PG 故障时 Redis fallback（本批 stub：降级为 MemorySaver，不假装接真 Redis）。"""
    # TODO(batch7+): 接入 RedisSaver；本批仅声明接口边界
    logger.warning("checkpointer_redis_fallback_stub")
    return MemorySaver()


# ── 归档 / 挂起钩子（本批仅声明签名，不实现调度）──────────────

async def archive_expired_sessions(older_than_days: int = 90) -> int:
    """归档超过 N 天的会话。本批 stub：返回 0，不实现定时调度。"""
    # TODO(batch7+): 实现会话 90 天归档策略
    return 0


async def handle_suspended_sessions(suspend_hours: int = 24) -> int:
    """处理挂起超过 N 小时的会话（转人工）。本批 stub：返回 0。"""
    # TODO(batch7+): 实现 24h 挂起转人工策略
    return 0
