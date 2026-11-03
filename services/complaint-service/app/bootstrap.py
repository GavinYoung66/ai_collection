"""进程级装配片段：HTTP 入口（main.py）与后台 worker 共用。

只放不依赖 FastAPI 的状态装配，避免 worker 为了接 hooks 而导入 Web 应用。
"""
from __future__ import annotations


def wire_resilience_hooks() -> None:
    """G7：把 common.resilience.hooks 事件路由到本服务 publisher。

    - hooks 注入异步适配器（publisher.publish 为同步），事件名 csmp.{component}.{action}；
    - publisher 缺席/抛错时 hooks 自带回退 structlog，不阻断业务（spec session-service）；
    - shutdown 时 set_publisher(None) 恢复默认，防测试间泄漏。
    """
    from common.resilience import hooks

    from app.events.publisher import publisher

    async def _publish(name: str, payload: dict) -> None:
        publisher.publish(name, payload)

    hooks.set_publisher(_publish)


def unwire_resilience_hooks() -> None:
    from common.resilience import hooks

    hooks.set_publisher(None)
