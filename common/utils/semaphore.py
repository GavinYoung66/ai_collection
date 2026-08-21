"""并发限流信号量。"""
import asyncio
from collections.abc import AsyncGenerator

from common.config.settings import settings


class CallSemaphore:
    """asyncio.Semaphore 封装，用于外呼并发限流。"""

    def __init__(self, max_concurrency: int | None = None) -> None:
        limit = max_concurrency or settings.CALL_MAX_CONCURRENCY
        self._semaphore = asyncio.Semaphore(limit)

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._semaphore.release()

    async def acquire(self) -> None:
        await self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()


_call_semaphore: CallSemaphore | None = None


def get_call_semaphore() -> CallSemaphore:
    global _call_semaphore
    if _call_semaphore is None:
        _call_semaphore = CallSemaphore()
    return _call_semaphore
