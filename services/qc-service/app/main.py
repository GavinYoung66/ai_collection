"""QC Service FastAPI 入口。业务路由前缀 /api/v1；GET /health 挂服务根。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.auth import register_auth_handlers
from app.errors import register_qc_exception_handlers
from app.events.subscriber import start_background_tasks
from app.routers.alerts import router as alerts_router
from app.routers.check import router as check_router
from app.routers.records import router as records_router
from app.routers.review import router as review_router
from app.routers.rules import router as rules_router
from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware

setup_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = await start_background_tasks()
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except Exception:
                pass


app = FastAPI(title="QC Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(TraceMiddleware)
register_exception_handlers(app)
register_qc_exception_handlers(app)
register_auth_handlers(app)
app.include_router(health_router)
app.include_router(check_router)
app.include_router(records_router)
app.include_router(alerts_router)
app.include_router(review_router)
app.include_router(rules_router)
