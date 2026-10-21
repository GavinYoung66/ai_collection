"""Call Service FastAPI 入口。"""
from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.observability.metrics import mount_metrics
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router

setup_logging()
app = FastAPI(title="Call Service", version="1.0.0")
app.add_middleware(TraceMiddleware)
# 统一 Prometheus 指标：必须在所有 add_middleware 之后调用，
# 确保 MetricsMiddleware 处于最外层（Metrics→Trace→路由）；
# 同时注册无需鉴权的 GET /metrics（原生 exposition，不包统一信封）。
mount_metrics(app, service_name="call-service")
register_exception_handlers(app)
app.include_router(health_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "call-service", "version": "1.0.0"}
