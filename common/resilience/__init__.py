"""公共韧性原语零件库（三服务 import 复用，不含领域降级策略）。

零件清单：
- ``rate_limit``   固定窗口限流（Redis INCR+EXPIRE），Redis 挂静默放行
- ``concurrency``  非阻塞有界并发（满即返回 False，不排队等待）
- ``queue``        有界队列（满 push 返回 False，不静默丢数据）
- ``idempotency``  幂等键（Redis SETNX+TTL），Redis 挂保守拒绝
- ``breaker``      熔断器状态机（closed→open→half-open→closed/open）
- ``hooks``        统一可观测事件钩子（默认 structlog，不依赖 publisher）
- ``timeout``      三级超时配置（connect/read/total）

超时层级约定：MCP ``total=30s`` < LLM ``total=120s`` < 投诉全局 ``30min``，
保证下游超时先于上游调用方触发。

设计约束：common 只放原子零件，领域降级策略（degrade_flow/degrade_llm/
degrade_mcp/fallback_engine）留各服务自行拼装。
"""
from common.resilience import (
    concurrency,
    hooks,
    idempotency,
    queue,
    rate_limit,
    timeout,
)
from common.resilience.breaker import (
    BreakerRegistry,
    CircuitBreaker,
    make_llm_breaker_key,
    make_mcp_breaker_key,
)
from common.resilience.concurrency import Semaphore
from common.resilience.queue import BoundedQueue
from common.resilience.timeout import Timeout

__all__ = [
    "rate_limit",
    "concurrency",
    "queue",
    "idempotency",
    "breaker",
    "hooks",
    "timeout",
    "CircuitBreaker",
    "BreakerRegistry",
    "Semaphore",
    "BoundedQueue",
    "Timeout",
    "make_llm_breaker_key",
    "make_mcp_breaker_key",
]
