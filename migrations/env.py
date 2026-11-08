"""Alembic 迁移环境配置，支持 async + 双 schema。"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from common.config.settings import settings
from common.db.base import Base
from common.db.models import AuditLog, LLMCallLog, StrategyVersion, User

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# T115：ConfigParser 默认开启 %(name)s 插值——DSN 中 URL 编码后的 `%xx`
# （如密码含空格 → %20）会被当成插值语法而报 ValueError。此处转义 `%` 为
# `%%`，读取时（ConfigParser.items/get 默认 raw=False）自动还原为单个 `%`。
config.set_main_option("sqlalchemy.url", settings.pg_dsn.replace("%", "%%"))


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async def run_async_migrations() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    if asyncio.get_event_loop().is_closed():
        asyncio.run(run_async_migrations())
    else:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
