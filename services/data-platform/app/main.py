"""Data Platform FastAPI 入口。"""
import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.observability.metrics import mount_metrics
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router
from app.api.qc_source import router as qc_source_router
from app.api.snapshot import router as snapshot_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期：启动 qc.check.completed 订阅 + 超时看门狗；关停时取消。"""
    # T03：JWT/AES 密钥为默认占位值时立即失败（生产必须由 KMS 注入）
    from common.config.settings import settings as common_settings
    from common.config.settings import validate_required_secrets

    validate_required_secrets(common_settings)

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
            except asyncio.CancelledError:
                # T61：CancelledError 是 BaseException 子类，except Exception 捕
                # 不住——不显式吞掉则 cancel 后关停中断、非 exit 0（对齐
                # complaint-service worker._shutdown 的 suppress 形态）
                pass
            except Exception:
                # T40：后台任务终止异常 MUST NOT 静默丢弃——留 warning + 异常栈。
                logger.warning("dp_background_task_failed_on_shutdown", exc_info=True)
        # T57：关闭共享 LLM HTTP client（snapshot 序列化走 LLM 通道时创建；
        # 旧 loop 残留由 aclose 内部防御性 best-effort 关闭，失败不阻断关停）
        from common.llm.transport import aclose_shared_client

        await aclose_shared_client()
        # W4：关闭 Redis 连接池（job/result 快照缓存；仅 reset 会留池到进程退出）
        from app.core.redis import aclose_redis

        await aclose_redis()
        # T58：释放 SQLAlchemy engine 连接池（PG 连接归零）
        try:
            from common.db.session import engine

            await engine.dispose()
        except Exception as exc:  # noqa: BLE001 — 关停单点失败不中断
            logger.warning("dp_engine_dispose_failed", error=str(exc))


setup_logging()
app = FastAPI(title="Data Platform", version="1.0.0", lifespan=lifespan)
app.add_middleware(TraceMiddleware)
# 统一 Prometheus 指标：必须在所有 add_middleware 之后调用，
# 确保 MetricsMiddleware 处于最外层（Metrics→Trace→路由）；
# 同时注册无需鉴权的 GET /metrics（原生 exposition，不包统一信封）。
mount_metrics(app, service_name="data-platform")
register_exception_handlers(app)
app.include_router(health_router, prefix="/api/v1")
app.include_router(qc_source_router)
app.include_router(snapshot_router)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "data-platform", "version": "1.0.0"}
