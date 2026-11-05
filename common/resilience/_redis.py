"""resilience 零件共享的 Redis 客户端（私有模块，不对服务暴露接口承诺）。

- 首次使用时按 common settings 懒创建，短 socket 超时防止 Redis 挂起拖慢调用方；
- 测试/服务可通过 ``configure_redis`` 注入自定义客户端（fakeredis/独立连接池）；
- rate_limit 与 idempotency 共用同一连接池，避免每零件一个池。
"""
from __future__ import annotations

import asyncio

import structlog

from redis.asyncio import Redis

from common.config.settings import settings

logger = structlog.get_logger()

_client: Redis | None = None


def configure_redis(client: Redis | None) -> None:
    """注入 Redis 客户端（主要用于测试与多 Redis 实例服务）。"""
    global _client
    _client = client


def reset_redis() -> None:
    """清除注入/缓存的客户端，恢复懒创建（测试隔离用，不关闭连接）。"""
    global _client
    _client = None


async def aclose_redis() -> None:
    """关闭当前缓存的 Redis 客户端并恢复懒创建（W4：shutdown 清单登记项）。

    - best-effort：注入的测试替身可能无 aclose/close（fakeredis 等），
      以 getattr 探测，无可关闭出口或关闭失败均静默通过，不阻断关停；
    - 语义与 ``reset_redis`` 的差别：本函数真实释放底层连接池，仅用于
      宿主服务 lifespan shutdown；测试隔离请继续用 reset_redis。
    """
    global _client
    client, _client = _client, None
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
        logger.warning("resilience_redis_aclose_failed", exc_info=True)


def get_redis() -> Redis:
    """懒创建带短超时的 Redis 客户端。"""
    global _client
    if _client is None:
        _client = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            db=settings.REDIS_DB,
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return _client
