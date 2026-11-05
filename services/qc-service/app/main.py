"""QC Service FastAPI 入口。业务路由前缀 /api/v1；GET /health 挂服务根。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

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
logger = structlog.get_logger()


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


async def _unwire_resilience_redis() -> None:
    from app.deps import reset_redis as reset_qc_redis
    from common.resilience._redis import aclose_redis
    from common.resilience._redis import reset_redis

    # W4：resilience 注入的客户端即 qc deps 的 Redis 连接池——先真实关闭，
    # 再清引用（仅 reset 会把连接池留到进程退出）。
    await aclose_redis()
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

    # T03：JWT/AES 密钥为默认占位值时立即失败（生产必须由 KMS 注入）
    from common.config.settings import validate_required_secrets

    validate_required_secrets(platform_settings)

    configure_langsmith_env(platform_settings)
    configure_langfuse_env(platform_settings)
    # T02：认证模式启动日志（生产强制开启；本地关闭时显著告警）
    from common.security.rbac import log_auth_mode

    log_auth_mode(platform_settings, "qc-service")
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
            except asyncio.CancelledError:
                # T61：CancelledError 是 BaseException 子类，except Exception 捕
                # 不住——不显式吞掉则 cancel 后关停中断、非 exit 0（对齐
                # complaint-service worker._shutdown 的 suppress 形态）
                pass
            except Exception:
                # T40：后台任务终止异常 MUST NOT 静默丢弃——留 warning + 异常栈。
                logger.warning("qc_background_task_failed_on_shutdown", exc_info=True)
        _unwire_resilience_hooks()
        await _unwire_resilience_redis()
        # ── shutdown 资源清单（批 5 逐项登记，`资源名 → 释放入口`）──
        # _dp_client → await aclose_dp_client()（T50：DpClient lifespan 级单例）
        # llm_shared_client → await aclose_shared_client()（T57：共享 LLM HTTP client）
        # db_engine → await engine.dispose()（T58：PG 连接池归零）
        # redis_client → await aclose_redis()（W4：resilience/deps 共享 Redis 连接池）
        from app.deps import aclose_dp_client
        from common.db.session import engine
        from common.llm.transport import aclose_shared_client

        await aclose_dp_client()
        await aclose_shared_client()
        try:
            await engine.dispose()
        except Exception as exc:  # noqa: BLE001 — 关停单点失败不中断
            logger.warning("qc_engine_dispose_failed", error=str(exc))


app = FastAPI(title="QC Service", version="1.0.0", lifespan=lifespan)
# 跨域消费者（本地静态演示页）已移除；真实前端经网关同源访问，无需 CORS 中间件。
app.add_middleware(TraceMiddleware)
# 统一 Prometheus 指标：必须在所有 add_middleware 之后调用，
# 确保 MetricsMiddleware 处于最外层（Metrics→Trace→路由）；
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
