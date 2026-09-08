"""LLM 熔断状态管理。

复用 common.llm.circuit_breaker（pybreaker 实例），熔断时写 Redis csmp:llm_degraded=1，
恢复时删除该 Key。提供 is_degraded() 供调度 Agent 查询。

redis_client=None 时使用内存 stub（不依赖真实 Redis）。
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from app.utils.redis_keys import LLM_DEGRADED_KEY, TTL_LLM_DEGRADED

logger = structlog.get_logger()


class LLMBreaker:
    """LLM 熔断器状态管理。

    Args:
        redis_client: Redis 客户端。None 时用内存 stub（单测/无 Redis 环境）。
        breaker: pybreaker CircuitBreaker 实例。默认复用 common.llm.circuit_breaker._llm_breaker。
    """

    def __init__(self, redis_client: Any | None = None, breaker: Any | None = None):
        self._redis = redis_client
        if breaker is None:
            from common.llm.circuit_breaker import _llm_breaker

            breaker = _llm_breaker
        self._breaker = breaker
        # 注册熔断开闭回调
        self._breaker.add_listener(self)

    # ── pybreaker listener 回调 ──
    def state_change(self, cb: Any, old_state: Any, new_state: Any) -> None:
        """熔断器状态变化回调。"""
        new_name = getattr(new_state, "name", str(new_state))
        if new_name == "open":
            logger.warning("llm_breaker_open")
            self._set_degraded_flag()
        elif new_name == "closed":
            logger.info("llm_breaker_closed")
            self._clear_degraded_flag()

    # ── 对外接口 ──
    def is_degraded(self) -> bool:
        """查询当前是否熔断降级。"""
        if self._redis is not None:
            try:
                return bool(self._redis.get(LLM_DEGRADED_KEY))
            except Exception as e:
                logger.warning("redis_get_failed", key=LLM_DEGRADED_KEY, error=str(e))
                return self._breaker.current_state == "open"
        return self._breaker.current_state == "open"

    def trip(self) -> None:
        """手动触发熔断（测试用）。"""
        self._breaker.open()

    def reset(self) -> None:
        """手动恢复（测试用）。"""
        self._breaker.close()

    # ── 内部 ──
    def _set_degraded_flag(self) -> None:
        if self._redis is not None:
            try:
                if TTL_LLM_DEGRADED is None:
                    self._redis.set(LLM_DEGRADED_KEY, "1")
                else:
                    self._redis.setex(LLM_DEGRADED_KEY, TTL_LLM_DEGRADED, "1")
            except Exception as e:
                logger.warning("redis_set_failed", key=LLM_DEGRADED_KEY, error=str(e))

    def _clear_degraded_flag(self) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(LLM_DEGRADED_KEY)
            except Exception as e:
                logger.warning("redis_del_failed", key=LLM_DEGRADED_KEY, error=str(e))
