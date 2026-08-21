"""Call Service FastAPI 入口。"""
from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router

setup_logging()
app = FastAPI(title="Call Service", version="1.0.0")
app.add_middleware(TraceMiddleware)
register_exception_handlers(app)
app.include_router(health_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "call-service", "version": "1.0.0"}
