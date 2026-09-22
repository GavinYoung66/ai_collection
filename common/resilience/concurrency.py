"""非阻塞有界并发原语（封装 asyncio.Semaphore）。

与 ``common/utils/semaphore.py`` 的阻塞外呼信号量语义不同、两者共存：

- 外呼场景：容量满时排队等待（阻塞 acquire）；
- Agent 编排场景：容量满时立即返回 False，由调用方走降级，避免请求堆积。

``asyncio.Semaphore`` 无原生 try_acquire：这里利用 ``locked()`` 与 ``acquire()``
之间不存在 await 挂起点（同一事件循环步骤内原子完成检查与扣减）实现非阻塞获取。
另自记在途计数：asyncio.Semaphore 超量 release 不会报错（无 BoundedSemaphore），
不防护会静默抬高容量上限。
"""
from __future__ import annotations

import asyncio


class Semaphore:
    """满即拒绝的信号量。``acquire()`` 永不等待，容量满立即返回 False。"""

    def __init__(self, max_inflight: int) -> None:
        if not isinstance(max_inflight, int) or max_inflight <= 0:
            raise ValueError(f"max_inflight 必须为正整数，实际: {max_inflight!r}")
        self._max_inflight = max_inflight
        self._semaphore = asyncio.Semaphore(max_inflight)
        self._inflight = 0

    @property
    def max_inflight(self) -> int:
        return self._max_inflight

    @property
    def inflight(self) -> int:
        return self._inflight

    async def acquire(self) -> bool:
        """尝试占用一个槽位：成功 True，已满立即 False（不阻塞等待）。"""
        if self._semaphore.locked():
            return False
        # locked() 检查与下方扣减之间无 await，事件循环不会插入其他任务，
        # 因此此处 acquire 必然立即成功（许可恰好够），不会在满时挂起。
        await self._semaphore.acquire()
        self._inflight += 1
        return True

    def release(self) -> None:
        """释放一个槽位。未获取先释放抛 ValueError，防止容量被静默抬高。"""
        if self._inflight == 0:
            raise ValueError("Semaphore released too many times")
        self._semaphore.release()
        self._inflight -= 1
