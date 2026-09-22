"""幂等键原语：Redis SETNX + TTL。

- 首次 ``acquire`` 返回 True；同 key 未过期前再次获取返回 False，防止重试重复写入；
- Redis 不可用/超时时**保守拒绝**（返回 False）——幂等保护的是正确性，
  放行可能导致资金/工单类不可逆重复操作；
- 与 ``rate_limit`` 的"Redis 挂放行"方向相反，按业务后果定向设计。
"""
from __future__ import annotations

import structlog
from redis.exceptions import RedisError

from common.resilience._redis import get_redis

logger = structlog.get_logger()


async def acquire(key: str, ttl: int = 1800) -> bool:
    """尝试占用幂等键。True=首次占用成功，False=重复请求或 Redis 故障（保守拒绝）。"""
    try:
        client = get_redis()
        acquired = await client.set(key, "1", nx=True, ex=ttl)
        return bool(acquired)
    except (RedisError, OSError) as e:
        logger.warning("idempotency_degraded_reject", key=key, error=str(e))
        return False
