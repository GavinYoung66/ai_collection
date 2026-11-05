"""Redis 客户端：平台 REDIS_HOST/PORT/PASSWORD + QC_REDIS_DB（默认 3）。

与 qc-service app.deps.build_redis 同构，保证两端连到同一实例同一逻辑库。
"""
from __future__ import annotations

import asyncio

import structlog
from redis.asyncio import Redis

from app.core.config import dp_settings
from common.config.settings import settings as platform_settings

logger = structlog.get_logger()

_redis_client: Redis | None = None


def build_redis() -> Redis:
    kwargs: dict[str, object] = {
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


async def aclose_redis() -> None:
    """关闭缓存的 Redis 客户端并恢复懒创建（W4：dp shutdown 清单登记项）。

    best-effort：关闭失败仅留 warning 不阻断关停；测试注入替身无关闭
    出口时静默通过。
    """
    global _redis_client
    client, _redis_client = _redis_client, None
    if client is None:
        return
    closer = getattr(client, "aclose", None) or getattr(client, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        # T40：shutdown 释放失败不静默——留 warning，不阻断关停
        logger.warning("dp_redis_aclose_failed", exc_info=True)
