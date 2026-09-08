"""API 路由汇总导出。"""
from app.api.routes.complaints import router as complaints_router
from app.api.routes.mode import router as mode_router
from app.api.routes.stats import router as stats_router

__all__ = ["complaints_router", "mode_router", "stats_router"]
