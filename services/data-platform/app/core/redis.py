"""Redis 客户端：平台 REDIS_HOST/PORT/PASSWORD + QC_REDIS_DB（默认 3）。

与 qc-service app.deps.build_redis 同构，保证两端连到同一实例同一逻辑库。
"""
from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from app.core.config import dp_settings
from common.config.settings import settings as platform_settings

_redis_client: Redis | None = None


def build_redis() -> Redis:
    kwargs: dict[str, Any] = {
        "host": platform_settings.REDIS_HOST,
        "port": platform_settings.REDIS_PORT,
        "db": dp_settings.QC_REDIS_DB,
        "decode_responses": True,
    }
    if platform_settings.REDIS_PASSWORD:
        kwargs["password"] = platform_settings.REDIS_PASSWORD
    return Redis(**kwargs)


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = build_redis()
    return _redis_client


def reset_redis() -> None:
    global _redis_client
    _redis_client = None
