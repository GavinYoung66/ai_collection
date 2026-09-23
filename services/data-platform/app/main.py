"""Data Platform FastAPI 入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router
from app.api.qc_source import router as qc_source_router
from app.api.snapshot import router as snapshot_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期：启动 qc.check.completed 订阅 + 超时看门狗；关停时取消。"""
    from app.events.subscriber import start_background_tasks

    tasks = await start_background_tasks()
    app.state.dp_tasks = tasks
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except Exception:
                pass


setup_logging()
app = FastAPI(title="Data Platform", version="1.0.0", lifespan=lifespan)
app.add_middleware(TraceMiddleware)
register_exception_handlers(app)
app.include_router(health_router, prefix="/api/v1")
app.include_router(qc_source_router)
app.include_router(snapshot_router)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "data-platform", "version": "1.0.0"}
