"""兼容入口：LLM 调用重试统一走 ``common.resilience.retry``（T28）。"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from common.resilience.retry import LLM_RETRY, retry_async

T = TypeVar("T")


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = LLM_RETRY.max_attempts,
    delay: float = LLM_RETRY.base_delay,
    backoff: float = LLM_RETRY.backoff,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """对无参 async 工厂做指数退避重试；参数默认值来自统一策略。"""
    policy = LLM_RETRY.with_overrides(
        max_attempts=max_attempts, base_delay=delay, backoff=backoff
    )
    return await retry_async(fn, policy=policy, exceptions=exceptions)
