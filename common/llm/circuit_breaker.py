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


_llm_breaker.on_open(_on_open)
_llm_breaker.on_close(_on_close)

llm_breaker = _llm_breaker.decorate
