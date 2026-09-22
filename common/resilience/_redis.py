"""resilience 零件共享的 Redis 客户端（私有模块，不对服务暴露接口承诺）。

- 首次使用时按 common settings 懒创建，短 socket 超时防止 Redis 挂起拖慢调用方；
- 测试/服务可通过 ``configure_redis`` 注入自定义客户端（fakeredis/独立连接池）；
- rate_limit 与 idempotency 共用同一连接池，避免每零件一个池。
"""
from __future__ import annotations

from redis.asyncio import Redis

from common.config.settings import settings

_client: Redis | None = None


def configure_redis(client: Redis | None) -> None:
    """注入 Redis 客户端（主要用于测试与多 Redis 实例服务）。"""
    global _client
    _client = client


def reset_redis() -> None:
    """清除注入/缓存的客户端，恢复懒创建（测试隔离用）。"""
    global _client
    _client = None


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
