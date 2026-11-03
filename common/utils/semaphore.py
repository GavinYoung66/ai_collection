"""并发限流信号量（并发上限由调用方注入，common 不承载业务配置）。"""
import asyncio


class CallSemaphore:
    """asyncio.Semaphore 封装，用于外呼并发限流。"""

    def __init__(self, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._semaphore.release()

    async def acquire(self) -> None:
        await self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()


_call_semaphore: CallSemaphore | None = None


def get_call_semaphore(max_concurrency: int) -> CallSemaphore:
    global _call_semaphore
    if _call_semaphore is None:
        _call_semaphore = CallSemaphore(max_concurrency)
    return _call_semaphore
