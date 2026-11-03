"""Redis 服务前缀降级标志（单一职责：连续失败置位 / 成功清零）。

- 多实例以 Redis degrade 为准；Redis 挂时全部路径静默容错，不阻断主流程；
- 置位 key 必须带服务前缀（如 ``{svc}:llm_degraded:{model}``），TTL 到期自动恢复。
"""
from __future__ import annotations

from redis.exceptions import RedisError

from common.resilience._redis import get_redis

_DEGRADE_TTL_SECONDS = 60  # primary 降级标志 TTL


class RedisDegradeTracker:
    """服务前缀 Redis 降级：连续失败达阈值后 setex；成功清零本地计数。"""

    def __init__(
        self,
        redis,
        *,
        key: str,
        failure_threshold: int = 3,
        ttl_seconds: int = 60,
    ):
        self._redis = redis
        self.key = key
        self.failure_threshold = failure_threshold
        self.ttl_seconds = ttl_seconds
        self._failure_count = 0

    async def is_degraded(self) -> bool:
        if self._redis is None:
            return False
        try:
            return await self._redis.exists(self.key) > 0
        except (RedisError, OSError, AttributeError):
            return False

    async def on_success(self) -> None:
        self._failure_count = 0
        if self._redis is None:
            return
        try:
            await self._redis.delete(self.key)
        except (RedisError, OSError, AttributeError):
            return

    async def on_failure(self, error: BaseException | None = None) -> None:
        self._failure_count += 1
        if self._failure_count < self.failure_threshold:
            return
        if self._redis is None:
            return
        try:
            await self._redis.setex(self.key, self.ttl_seconds, "1")
        except (RedisError, OSError, AttributeError):
            return


async def mark_degraded_flag(key: str, *, ttl_seconds: int = _DEGRADE_TTL_SECONDS) -> None:
    """置位服务前缀 Redis 降级标志（一次失败即标记，TTL 到期自动恢复）。

    Redis 挂时静默（RedisDegradeTracker 内部容错），不阻断编排主流程。
    当前 threshold=1：每次调用新建 tracker（无状态残留，语义即"失败即标记"）；
    若后续需要"连续 N 次失败才置位"，应改为注册表持有跨调用 tracker 实例。
    """
    tracker = RedisDegradeTracker(
        get_redis(), key=key, failure_threshold=1, ttl_seconds=ttl_seconds
    )
    await tracker.on_failure()
