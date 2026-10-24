"""QC Service FastAPI 入口。业务路由前缀 /api/v1；GET /health 挂服务根。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.auth import register_auth_handlers
from app.errors import register_qc_exception_handlers
from app.events.subscriber import start_background_tasks
from app.mcp.http import router as mcp_router
from app.routers.alerts import router as alerts_router
from app.routers.check import router as check_router
from app.routers.demo import router as demo_router
from app.routers.llm_usage import router as llm_usage_router
from app.routers.records import router as records_router
from app.routers.review import router as review_router
from app.routers.rules import router as rules_router
from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.observability.metrics import mount_metrics
from common.trace.middleware import TraceMiddleware

setup_logging()


def wire_resilience_hooks() -> None:
    """把 common.resilience.hooks 接到 QC 专用适配器（记录 qc.*，不进六频道 Redis）。

    shutdown 必须 set_publisher(None)，避免测试间泄漏。
    """
    from app.events.resilience import on_resilience_event
    from common.resilience import hooks

    hooks.set_publisher(on_resilience_event)


def _unwire_resilience_hooks() -> None:
    from common.resilience import hooks

    hooks.set_publisher(None)


async def wire_resilience_redis() -> None:
    """把 QC Redis（DB 3）注入 common 零件，供限流/后续 invoke_llm 共用。"""
    from app.deps import get_redis as qc_get_redis
    from common.resilience._redis import configure_redis

    configure_redis(await qc_get_redis())


def _unwire_resilience_redis() -> None:
    from app.deps import reset_redis as reset_qc_redis
    from common.resilience._redis import reset_redis

    reset_redis()
    reset_qc_redis()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 链路追踪兜底：统一开关关闭时清除 LangSmith/LangChain SDK 环境变量，
    # 防止 SDK 读 os.environ 绕过闸门自动上报（与 rag-service 对齐）。
    from common.config.settings import settings as platform_settings
    from common.llm.reliability import set_default_service
    from common.trace.langfuse_gate import configure_langfuse_env
    from common.trace.langsmith_gate import configure_langsmith_env

    configure_langsmith_env(platform_settings)
    configure_langfuse_env(platform_settings)
    set_default_service("qc")
    wire_resilience_hooks()
    await wire_resilience_redis()

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
        _unwire_resilience_hooks()
        _unwire_resilience_redis()


app = FastAPI(title="QC Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TraceMiddleware)
# 统一 Prometheus 指标：必须在所有 add_middleware 之后调用，
# 确保 MetricsMiddleware 处于最外层（Metrics→Trace→CORS→路由）；
# 同时注册无需鉴权的 GET /metrics（原生 exposition，不包统一信封）。
mount_metrics(app, service_name="qc-service")
register_exception_handlers(app)
register_qc_exception_handlers(app)
register_auth_handlers(app)
app.include_router(health_router)
app.include_router(check_router)
app.include_router(demo_router)
app.include_router(records_router)
app.include_router(alerts_router)
app.include_router(review_router)
app.include_router(rules_router)
app.include_router(llm_usage_router)
app.include_router(mcp_router)
