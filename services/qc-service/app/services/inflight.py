"""离线开检并发闸：P0 Semaphore 满即 False，本服务 acquire_wait 排队等槽。"""
from __future__ import annotations

import asyncio

from app.core.config import qc_settings
from common.resilience.concurrency import Semaphore


class InflightGate:
    """满则 Condition 等待；成功 acquire 后由调用方 try/finally release。"""

    def __init__(self, max_inflight: int, wait_timeout: float = 0) -> None:
        self._sem = Semaphore(max_inflight)
        self._cv = asyncio.Condition()
        self._wait_timeout = float(wait_timeout)

    @property
    def max_inflight(self) -> int:
        return self._sem.max_inflight

    @property
    def inflight(self) -> int:
        return self._sem.inflight

    async def acquire_wait(self) -> None:
        """直到拿到槽位。timeout>0 到期后继续等，永不无槽硬跑、不因此 500。"""
        async with self._cv:
            while not await self._sem.acquire():
                if self._wait_timeout > 0:
                    try:
                        await asyncio.wait_for(self._cv.wait(), timeout=self._wait_timeout)
                    except TimeoutError:
                        continue
                else:
                    await self._cv.wait()

    async def release(self) -> None:
        self._sem.release()
        async with self._cv:
            self._cv.notify(1)


_gate: InflightGate | None = None


def get_inflight_gate() -> InflightGate:
    global _gate
    if _gate is None:
        _gate = InflightGate(
            int(qc_settings.QC_MAX_INFLIGHT),
            float(qc_settings.QC_INFLIGHT_WAIT_TIMEOUT),
        )
    return _gate


def reset_inflight_gate() -> None:
    global _gate
    _gate = None
