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
    }
