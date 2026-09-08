"""LLM 调用可靠性：重试 + Redis 降级约定（在 transport 之上组合）。

- Transport：只发 HTTP
- 本模块：bounded retry、服务前缀 degrade key
- 多实例以 Redis degrade 为准；进程内 pybreaker 仅可选辅助
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from redis.exceptions import RedisError

T = TypeVar("T")

PLACEHOLDER_SECRETS = frozenset(
    {"", "change_me", "<change_me>", "dev_cot_key_stub_change_me"}
)


def is_placeholder_secret(value: str | None) -> bool:
    """已知占位符密钥视为未配置。"""
    return (value or "").strip() in PLACEHOLDER_SECRETS


def require_real_secret(value: str | None, *, label: str = "API key") -> str:
    """真路径：占位或空则 fail-closed。"""
    text = (value or "").strip()
    if is_placeholder_secret(text):
        raise RuntimeError(f"{label} is missing or placeholder (change_me)")
    return text


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


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """对无参 async 工厂做指数退避重试。"""
    last: BaseException | None = None
    current = delay
    for attempt in range(max_attempts):
        try:
            return await fn()
        except exceptions as e:
            last = e
            if attempt >= max_attempts - 1:
                break
            await asyncio.sleep(current)
            current *= backoff
    assert last is not None
    raise last
