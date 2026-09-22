"""有界队列原语：满则拒绝，绝不静默丢数据。

- ``push`` 队列满时返回 False，item 不入队，由调用方决定拒绝动作；
- ``pop`` 超时返回 None。

``full()`` 检查与 ``put_nowait`` 之间无 await，同一事件循环步骤内原子，
不存在检查后入队前被插队导致溢出的窗口。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional


class BoundedQueue:
    def __init__(self, max_len: int) -> None:
        if not isinstance(max_len, int) or max_len <= 0:
            raise ValueError(f"max_len 必须为正整数，实际: {max_len!r}")
        self._max_len = max_len
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_len)

    @property
    def max_len(self) -> int:
        return self._max_len

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def full(self) -> bool:
        return self._queue.full()

    async def push(self, item: Any) -> bool:
        """入队：未满 True；已满返回 False 且 item 不入队（不静默丢弃）。"""
        if self._queue.full():
            return False
        self._queue.put_nowait(item)
        return True

    async def pop(self, timeout: float = 1.0) -> Optional[Any]:
        """出队：空队列等待 timeout 秒仍无数据则返回 None。

        取到 item 后立即 ``task_done()``（取出即完成），支持
        ``asyncio.Queue.join()`` 优雅关停语义。
        """
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        self._queue.task_done()
        return item

    def pop_nowait(self) -> Optional[Any]:
        """非阻塞出队：空队列立即返回 None（批量消费轮询用）。"""
        try:
            item = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        self._queue.task_done()
        return item
