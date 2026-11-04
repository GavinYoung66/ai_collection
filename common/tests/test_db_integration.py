"""T38：common.db 真实 PostgreSQL 集成测试。

需真实 PG：`COMMON_DB_TESTS=1`（对齐 rag-service `RAG_DB_TESTS` /
complaint-service `COMPLAINT_DB_TESTS` 约定），DSN 由 `PG_*` 环境变量派生
（见 ``common.config.settings.Settings.pg_dsn``）。开关关闭时整文件 skip，
不影响常规单测回归。

与 ``test_db.py`` 的分工：test_db.py 保留纯结构契约断言（inspect，不连库），
按任务要求不得删除；本文件走真实引擎路径（create_async_engine → 建表 →
写入 → 读回 → 更新 → 清理），验证 ORM 映射在真实 PG 上可执行，而非仅结构自洽。
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture()
def pg_required():
    """真实 PG 集成开关：`COMMON_DB_TESTS=1` 才执行，否则 skip。"""
    if os.getenv("COMMON_DB_TESTS") != "1":
        pytest.skip("COMMON_DB_TESTS != 1，跳过需要真实 PG 的集成用例")


@pytest.fixture()
async def pg_engine(pg_required):
    """独立引擎：不经 common.db.session 模块级单例，测试结束确定性 dispose。"""
    from common.config.settings import settings
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.pg_dsn, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture()
async def pg_tables(pg_engine):
    """按 common ORM 元数据建表（checkfirst 幂等，不触碰既有数据与结构）。"""
    from common.db.base import Base
    from sqlalchemy.ext.asyncio import inspect as sa_inspect  # noqa: F401  保留说明：create_all 自带 checkfirst

    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def test_real_pg_user_roundtrip(pg_engine, pg_tables):
    """真实 PG 全链路：写入 → 读回 → 更新 → 删除，ORM 映射真实可执行。

    覆盖：
    - UUID 主键 / JSONB / timestamptz 服务端默认值在真实 PG 上成立；
    - ``create_time`` 时区感知（T39 时间确定性口径在持久层的一致性）；
    - 用例自清理（按唯一 username 删除），不污染目标库。
    """
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from common.db.models import User

    factory = async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
    username = f"it_{uuid.uuid4().hex[:12]}"
    async with factory() as session:
        session.add(
            User(
                username=username,
                password_hash="bcrypt-integration-placeholder",
                real_name="集成测试",
                role="agent",
            )
        )
        await session.commit()

        try:
            found = (
                (await session.execute(select(User).where(User.username == username)))
                .scalars()
                .all()
            )
            assert len(found) == 1
            row = found[0]
            assert row.id is not None
            assert row.create_time.tzinfo is not None
            assert row.status == "active"
            assert row.agent_skills == []

            row.last_login_at = row.create_time
            await session.commit()
            reread = (
                (await session.execute(select(User).where(User.username == username)))
            ).scalar_one()
            assert reread.last_login_at is not None
        finally:
            await session.execute(sa_delete(User).where(User.username == username))
            await session.commit()
