"""异步数据库引擎 + 会话工厂。"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from common.config.settings import settings

engine = create_async_engine(
    settings.pg_dsn,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=(settings.ENV == "dev"),
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
