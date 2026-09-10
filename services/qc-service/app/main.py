"""QC Service FastAPI 入口。0.1 仅根 GET /health，不挂业务路由、不挂 /api/v1/health。"""
from fastapi import FastAPI

from app.api.health import router as health_router
from app.errors import register_qc_exception_handlers
from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware

setup_logging()
app = FastAPI(title="QC Service", version="1.0.0")
app.add_middleware(TraceMiddleware)
register_exception_handlers(app)
register_qc_exception_handlers(app)
app.include_router(health_router)
