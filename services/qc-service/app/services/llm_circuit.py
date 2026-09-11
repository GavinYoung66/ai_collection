"""qc LLM 熔断：Redis 键 qc:llm_degraded，60s 半开。不是 qc.llm.degraded 频道。"""
from __future__ import annotations

import time
from typing import Callable, Literal

from app.repositories.job_store import QcJobStore

State = Literal["closed", "open", "half_open"]


def is_timeout_or_5xx(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    msg = str(exc)
    if "HTTP 5" in msg:
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    return False


class QcLlmCircuit:
    def __init__(
        self,
        job_store: QcJobStore | None = None,
        *,
        fail_max: int = 3,
        reset_timeout: float = 60.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.job_store = job_store
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._now = now or time.monotonic
        self.state: State = "closed"
        self.failures = 0
        self.opened_at: float | None = None
        self.just_opened = False

    def _maybe_half_open(self) -> None:
        if self.state == "open" and self.opened_at is not None:
            if self._now() - self.opened_at >= self.reset_timeout:
                self.state = "half_open"

    def should_skip(self) -> bool:
        self._maybe_half_open()
        return self.state == "open"

    def allow_probe(self) -> bool:
        self._maybe_half_open()
        return self.state in {"closed", "half_open"}

    async def trip_open(self) -> bool:
        """超时/5xx 重试耗尽后 OPEN。返回是否新打开。"""
        newly = self.state != "open"
        self.failures = self.fail_max
        self.state = "open"
        self.opened_at = self._now()
        self.just_opened = newly
        if self.job_store is not None:
            await self.job_store.set_llm_degraded("1")
        return newly

    async def record_failure(self) -> bool:
        if self.state == "half_open":
            return await self.trip_open()
        self.failures += 1
        if self.failures >= self.fail_max:
            return await self.trip_open()
        self.just_opened = False
        return False

    async def record_success(self) -> None:
        was_openish = self.state in {"open", "half_open"}
        self.failures = 0
        self.state = "closed"
        self.opened_at = None
        self.just_opened = False
        if was_openish and self.job_store is not None:
            await self.job_store.clear_llm_degraded()
