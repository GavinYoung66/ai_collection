"""健康检查路由。

响应含 pg / redis / mcp 探活状态，依赖未就绪时标记 degraded，HTTP 仍返回 200。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """健康检查：始终返回 200，降级状态通过响应体字段体现。"""
    app_state = request.app.state
    return {
        "status": "ok",
        "service": "complaint-service",
        "version": "1.0.0",
        "pg": "degraded" if getattr(app_state, "pg_degraded", False) else "ok",
        "redis": "degraded" if getattr(app_state, "redis_degraded", False) else "ok",
        "mcp": "degraded" if getattr(app_state, "mcp_degraded", False) else "ok",
        # postgres = 图断点跨进程/重启可恢复；memory = 仅进程内，重启即丢。
        # 这是生产上唯一能区分「真持久化」与「断点会丢」的信号，勿删。
        "checkpointer": getattr(app_state, "checkpointer_kind", "memory"),
        # session 写穿是否已降级（连续失败）。degraded 时状态机变更只在内存。
        "session_persist": "degraded" if _session_persist_degraded() else "ok",
    }


def _session_persist_degraded() -> bool:
    try:
        from app.service.session_service import session_service

        return session_service.persist_degraded
    except Exception:  # noqa: BLE001
        return False
