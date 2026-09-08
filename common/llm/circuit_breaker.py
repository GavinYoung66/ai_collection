"""LLM 熔断器，pybreaker 封装。"""
import functools
from typing import Callable, TypeVar

import structlog
from pybreaker import CircuitBreaker

from common.config.settings import settings

logger = structlog.get_logger()

T = TypeVar("T")

_llm_breaker = CircuitBreaker(
    fail_max=settings.LLM_CIRCUIT_BREAKER_THRESHOLD,
    reset_timeout=settings.LLM_CIRCUIT_BREAKER_RECOVERY,
    name="llm_breaker",
)


def _on_open():
    logger.warning("llm_circuit_open")


def _on_close():
    logger.info("llm_circuit_closed")


# pybreaker 正确 API：通过 listener 回调（原 on_open/on_close 方法不存在）
class _BreakerListener:
    def before_call(self, cb, func, *args, **kwargs):
        return

    def success(self, cb):
        return

    def failure(self, cb, exc):
        return

    def state_change(self, cb, old_state, new_state):
        new_name = getattr(new_state, "name", str(new_state))
        if new_name == "open":
            _on_open()
        elif new_name == "closed":
            _on_close()


_llm_breaker.add_listener(_BreakerListener())

llm_breaker = _llm_breaker  # CircuitBreaker 实例本身可作装饰器
