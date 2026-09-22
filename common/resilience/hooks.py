"""统一可观测事件钩子。

- 事件名约定：``{svc}.{component}.{action}`` 三段
  （如 ``csmp.rate_limit.hit`` / ``csmp.circuit_breaker.open`` / ``qc.queue.full``），
  也允许 2 段 ``{svc}.{action}``（如 spec 示例 ``complaint.degraded``）。
- 默认实现走 structlog，不依赖任何外部 publisher；
- 服务可注入自己的 publisher（如投诉服务发 ``complaint.degraded`` 事件），
  publisher 抛错不得影响调用方（降级回 structlog 日志）。
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger()

# 至少 2 段：{svc}.{action}（如 complaint.degraded）或 {svc}.{component}.{action}，
# 各段至少 1 个小写字母/数字/下划线/连字符
_EVENT_NAME_RE = re.compile(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+){1,}$")

EventPublisher = Callable[[str, dict[str, Any]], Awaitable[None]]

_publisher: EventPublisher | None = None


def set_publisher(publisher: EventPublisher | None) -> None:
    """注入服务自定义事件发布器；传 None 恢复默认 structlog 日志。"""
    global _publisher
    _publisher = publisher


def get_publisher() -> EventPublisher | None:
    return _publisher


async def on_event(name: str, payload: dict[str, Any]) -> None:
    """上报一个韧性事件。

    任何情况下都不向调用方抛异常：publisher 失败时降级为 structlog 日志。
    事件名不符合 ``{svc}.{component}.{action}``（或 2 段 ``{svc}.{action}``）
    约定时抛 ValueError（编程期错误，fail-fast 而非静默吞掉）。
    """
    if not isinstance(name, str) or not _EVENT_NAME_RE.match(name):
        raise ValueError(
            f"hook 事件名须为 {{svc}}.{{component}}.{{action}}（至少 2 段），"
            f"实际: {name!r}"
        )
    publisher = _publisher
    if publisher is not None:
        try:
            await publisher(name, payload)
            return
        except Exception as e:  # publisher 故障不影响业务/零件主流程
            logger.warning("hook_publisher_failed", name=name, error=str(e))
    # payload 整体嵌套，避免与 structlog 保留字段冲突导致二次异常
    logger.info("resilience_event", name=name, payload=payload)
