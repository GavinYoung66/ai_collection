"""Complaint Service FastAPI 入口。

批次1 脚手架：注册中间件/异常处理/路由，预留 PG/MCP 生命周期初始化（降级启动）。
"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router
from app.api.routes.complaints import router as complaints_router
from app.api.routes.mode import router as mode_router
from app.api.routes.stats import router as stats_router
from app.config import settings

setup_logging(env=getattr(settings, "ENV", "dev"), level=getattr(settings, "LOG_LEVEL", None))


def _probe_mcp_gateway() -> bool:
    """standalone 且非 Mock 时探活 MCP_GATEWAY_URL；失败返回 False（标记 mcp_degraded）。

    inline / Mock：不发起外网探活，视为可达。
    """
    mode = getattr(settings, "MCP_GATEWAY_MODE", "inline")
    if mode != "standalone":
        return True
    if getattr(settings, "USE_MOCK_MCP", True):
        return True
    gateway_url = (getattr(settings, "MCP_GATEWAY_URL", "") or "").rstrip("/")
    if not gateway_url:
        return False
    import urllib.error
    import urllib.request

    for path in ("/health", ""):
        try:
            with urllib.request.urlopen(f"{gateway_url}{path}", timeout=2) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    return False


def wire_resilience_hooks() -> None:
    """G7：把 common.resilience.hooks 事件路由到本服务 publisher。

    - hooks 注入异步适配器（publisher.publish 为同步），事件名 csmp.{component}.{action}；
    - publisher 缺席/抛错时 hooks 自带回退 structlog，不阻断业务（spec session-service）；
    - shutdown 时 set_publisher(None) 恢复默认，防测试间泄漏。
    """
    from common.resilience import hooks

    from app.events.publisher import publisher

    async def _publish(name: str, payload: dict) -> None:
        publisher.publish(name, payload)

    hooks.set_publisher(_publish)


def _unwire_resilience_hooks() -> None:
    from common.resilience import hooks

    hooks.set_publisher(None)


# 队列消费循环轮询间隔（秒）
_QUEUE_CONSUME_INTERVAL = 1.0

# 在途排队研判任务强引用集合：事件循环仅弱持有 asyncio.Task，
# 不保存返回值时任务可能在执行中被 GC（中途消失 → 槽/幂等锁不释放）。
# 任务完成后经 done_callback 自动移出集合。
_running_queued: set[asyncio.Task] = set()


def _spawn_queued_complaint(complaint_id: str) -> None:
    """派发排队工单研判并持有任务引用至其结束。"""
    task = asyncio.create_task(_run_queued_complaint(complaint_id))
    _running_queued.add(task)
    task.add_done_callback(_running_queued.discard)


async def _run_queued_complaint(complaint_id: str) -> None:
    """消费循环已为该工单预留并发槽：执行研判并释放槽 + 维5幂等锁。"""
    import structlog

    from app.service.complaint_service import complaint_service
    from app.service.rate_limiter import rate_limiter

    logger = structlog.get_logger()
    try:
        await complaint_service.judge_async(complaint_id, run_graph=True)
    except asyncio.CancelledError:
        # shutdown 取消：best-effort 把滞留 judging 的 session 推进 pending_review。
        # fail_shutdown 为无挂起点协程（内存态），此处 await 会原子完成，
        # 不会被本次取消信号二次打断；随后必须重新抛出以履行取消语义，
        # 由 finally 释放并发槽与维5锁。
        logger.warning("queue_consumer_judge_cancelled", complaint_id=complaint_id)
        try:
            marked = await complaint_service.fail_shutdown(complaint_id)
            if not marked:
                logger.info(
                    "queue_consumer_shutdown_mark_skipped",
                    complaint_id=complaint_id,
                    reason="session_not_in_pipeline",
                )
        except Exception as e:  # noqa: BLE001 — 关停兜底失败不阻塞槽/锁清理
            logger.warning(
                "queue_consumer_shutdown_mark_failed",
                complaint_id=complaint_id,
                error=str(e),
            )
        raise
    except Exception as e:  # noqa: BLE001 — 单工单失败不拖垮消费循环
        logger.warning("queue_consumer_judge_failed", complaint_id=complaint_id, error=str(e))
    finally:
        rate_limiter.release_complaint()
        await rate_limiter.release_complaint_lock(complaint_id)


async def _consume_queue_once() -> None:
    """消费一轮排队工单（G8 接线，供消费循环与测试复用）。

    - ready：已预留并发槽，直接派发执行；
    - expired：排队超 10min → A5010 转人审，并释放幂等锁。
    """
    import structlog

    from app.service.complaint_service import complaint_service
    from app.service.rate_limiter import rate_limiter

    logger = structlog.get_logger()
    ready, expired = await rate_limiter.consume_pending()
    for cid in expired:
        logger.warning("queue_consumer_expired", complaint_id=cid)
        try:
            await complaint_service.fail_queue_timeout(cid)
        except Exception as e:  # noqa: BLE001
            logger.warning("queue_consumer_expire_failed", complaint_id=cid, error=str(e))
    for cid in ready:
        _spawn_queued_complaint(cid)


async def _queue_consumer_loop() -> None:
    import structlog

    logger = structlog.get_logger()
    while True:
        try:
            await _consume_queue_once()
        except Exception as e:  # noqa: BLE001 — 循环永不退出
            logger.warning("queue_consumer_loop_error", error=str(e))
        await asyncio.sleep(_QUEUE_CONSUME_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """startup：真路径 MCP 配置 fail-fast；PG/MCP 探活；shutdown 清理。

    - USE_MOCK_MCP=false 且缺 RAG_SERVICE_URL：启动即失败（DEBT-04）
    - Mock 默认路径：不要求 rag-service
    """
    # ── startup ──
    # 链路追踪兜底：统一开关关闭时清除 LangSmith/LangChain SDK 环境变量，
    # 防止 LangGraph 等 SDK 读 os.environ 绕过闸门自动上报（与 rag-service 对齐）。
    from common.trace.langfuse_gate import configure_langfuse_env
    from common.trace.langsmith_gate import configure_langsmith_env

    configure_langsmith_env(settings)
    configure_langfuse_env(settings)

    # G7：韧性事件（csmp.{component}.{action}）统一路由到本服务 publisher
    wire_resilience_hooks()

    # G8：排队工单消费循环（动态消费 + 排队超时 A5010 弹出）
    consumer_task = asyncio.create_task(_queue_consumer_loop())
    app.state.queue_consumer = consumer_task

    app.state.pg_degraded = False
    app.state.redis_degraded = False
    app.state.mcp_degraded = False

    if not getattr(settings, "USE_MOCK_MCP", True):
        from app.mcp.validate import validate_real_mcp_config

        validate_real_mcp_config()

    if not getattr(settings, "USE_MOCK_LLM", True):
        from app.llm.validate import validate_real_llm_config

        validate_real_llm_config()

    # 预留 AsyncPostgresSaver 初始化（批次4 接入 LangGraph checkpointer 时实现）
    try:
        # TODO(批次4): 初始化 AsyncPostgresSaver，连接池 min_size=5, max_size=20
        pass
    except Exception:
        app.state.pg_degraded = True

    # MCP：standalone 且非 Mock 时探活网关，避免 health mcp 假绿
    try:
        if not _probe_mcp_gateway():
            app.state.mcp_degraded = True
    except Exception:
        app.state.mcp_degraded = True

    yield

    # ── shutdown ──
    consumer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer_task
    # 取消在途排队研判任务：其 finally 仍会释放并发槽与幂等锁
    for task in list(_running_queued):
        task.cancel()
    for task in list(_running_queued):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _unwire_resilience_hooks()
    # 预留连接池清理（批次4 接入后实现 engine.dispose() 等）
    try:
        pass
    except Exception:
        pass


app = FastAPI(title="complaint-service", version="1.0.0", lifespan=lifespan)
# 本地人审静态页（默认 :8080）跨域访问 :8006
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
register_exception_handlers(app)

# 健康检查：同时提供 /health 与 /api/v1/health
app.include_router(health_router, prefix="/api/v1")
app.include_router(health_router)

# 业务路由（前缀 /api/v1）
# 挂载顺序：stats → mode → complaints（固定路径 /stats /mode 先于 /{id}，防被吞）
app.include_router(stats_router, prefix="/api/v1")
app.include_router(mode_router, prefix="/api/v1")
app.include_router(complaints_router, prefix="/api/v1")
