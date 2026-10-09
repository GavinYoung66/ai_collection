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
from common.observability.metrics import mount_metrics
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

# 溢出补偿循环（阶段3，design D4）：BRPOP 空转/异常后的休眠间隔（防空转刷 Redis）；
# full 回塞前的指数退避起点与上限（上限 2s）。
_DEFERRED_IDLE_INTERVAL = 1.0
_DEFERRED_BACKOFF_BASE = 0.05
_DEFERRED_BACKOFF_MAX = 2.0

# 周期维护循环间隔（秒）：超时转人工 + checkpoint 三表裁剪。
# 5min 足够频繁（崩溃后最长 5min 内把滞留工单转人工），又不至于给 PG 加压。
_MAINTENANCE_INTERVAL = 300.0

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


async def _maintenance_loop() -> None:
    """周期维护循环：超时转人工 + checkpoint 三表裁剪。

    多副本下每个实例都会跑此循环，但 `run_maintenance` 内部有跨副本锁
    （maintenance_lock），保证同刻只有一个实例真正执行，其余跳过。
    """
    import structlog

    from app.service.maintenance import run_maintenance

    logger = structlog.get_logger()
    while True:
        try:
            await run_maintenance()
        except Exception as e:  # noqa: BLE001 — 循环永不退出
            logger.warning("maintenance_loop_error", error=str(e))
        await asyncio.sleep(_MAINTENANCE_INTERVAL)


async def _deferred_consumer_loop() -> None:
    """HTTP 队列满溢出 Redis 延迟队列补偿循环（阶段3，design D4）。

    仅开关开启时由 lifespan 启动。BRPOP 取单 → acquire_complaint 重新接入：
    - granted   → judge_async 执行（复用在途守卫/图锁/排队 deadline 幂等），
                  结束/异常/取消均释放并发槽 + 维5锁（与 complaints.py 同款）；
    - queued    → 已进内存队列，由既有 _queue_consumer_loop 接管，不做额外动作；
    - duplicate → 在途/已完成，幂等忽略（不重塞）；
    - full      → 指数退避（上限 2s）后重塞队尾，禁止无退避空转刷 Redis。

    关停不丢（3.5）：取出后未成功接入且未重塞即被取消/异常中断 → 塞回队列，
    保证重启后队列仍有该 cid（幂等由维5锁 + 图锁 + 在途集合保证只研判一次）。
    """
    import structlog

    from app.service.complaint_service import complaint_service
    from app.service.rate_limiter import (
        OUTCOME_DUPLICATE,
        OUTCOME_FULL,
        OUTCOME_GRANTED,
        OUTCOME_QUEUED,
        rate_limiter,
    )

    logger = structlog.get_logger()
    backoff = _DEFERRED_BACKOFF_BASE
    while True:
        cid: str | None = None
        settled = False  # 已明确处置：接入完成 / 已重塞 / 幂等忽略
        try:
            cid = await rate_limiter.deferred_pop()
            if cid is None:
                # 队列空（BRPOP 超时）或 Redis 故障：休眠防无退避空转刷 Redis
                settled = True
                backoff = _DEFERRED_BACKOFF_BASE
                await asyncio.sleep(_DEFERRED_IDLE_INTERVAL)
                continue
            outcome = await rate_limiter.acquire_complaint(cid)
            if outcome == OUTCOME_GRANTED:
                try:
                    await complaint_service.judge_async(cid, run_graph=True)
                except asyncio.CancelledError:
                    raise  # 关停中断 → 处置未完成，finally 重塞
                except Exception as e:  # noqa: BLE001 — 单工单失败不拖垮循环
                    logger.warning(
                        "deferred_consumer_judge_failed",
                        complaint_id=cid,
                        error=str(e),
                    )
                finally:
                    await rate_limiter.release_complaint_lock(cid)
                    rate_limiter.release_complaint()
                settled = True
            elif outcome == OUTCOME_QUEUED:
                settled = True  # 已进内存队列，由既有消费循环接管
            elif outcome == OUTCOME_FULL:
                # 仍满：指数退避后重塞队尾（LPUSH + BRPOP 保持 FIFO）
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _DEFERRED_BACKOFF_MAX)
                await rate_limiter.deferred_requeue(cid)
                settled = True
            elif outcome == OUTCOME_DUPLICATE:
                settled = True  # 在途/已完成，幂等忽略（不重塞）
        except asyncio.CancelledError:
            # 关停：已取出但未处置的 cid 必须重塞，保证重启后队列仍有该 cid。
            # shield 保证第二次 cancel 只打断本任务，LPUSH 协程仍被事件循环
            # 调度完成（毫秒级，lifespan 后续关停步骤期间即可落盘）。
            if cid is not None and not settled:
                with contextlib.suppress(Exception):
                    await asyncio.shield(rate_limiter.deferred_requeue(cid))
            raise
        except Exception as e:  # noqa: BLE001 — 循环永不退出
            logger.warning(
                "deferred_consumer_loop_error", complaint_id=cid, error=str(e)
            )
            if cid is not None and not settled:
                with contextlib.suppress(Exception):
                    await rate_limiter.deferred_requeue(cid)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _DEFERRED_BACKOFF_MAX)


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

    # 周期维护循环：超时转人工（崩溃/重启后从 PG 恢复滞留工单）+ 三表裁剪
    maintenance_task = asyncio.create_task(_maintenance_loop())
    app.state.maintenance = maintenance_task

    # 溢出补偿循环（阶段3，design D4）：默认关闭不启动（现状零变化，也不空转刷 Redis）
    if settings.COMPLAINT_DEFERRED_QUEUE_ENABLED:
        deferred_task: asyncio.Task | None = asyncio.create_task(
            _deferred_consumer_loop()
        )
    else:
        deferred_task = None
    app.state.deferred_consumer = deferred_task

    # 4.4：EventBus 业务订阅（含 complaint.created 占位 handler）；失败不阻断启动
    from app.events.subscriber import start_eventbus_subscriber

    eventbus_task = await start_eventbus_subscriber()
    app.state.eventbus_subscriber = eventbus_task

    app.state.pg_degraded = False
    app.state.redis_degraded = False
    app.state.mcp_degraded = False

    if not getattr(settings, "USE_MOCK_MCP", True):
        from app.mcp.validate import validate_real_mcp_config

        validate_real_mcp_config()

    if not getattr(settings, "USE_MOCK_LLM", True):
        from app.llm.validate import validate_real_llm_config

        validate_real_llm_config()

    # 批次4：LangGraph checkpointer 初始化。
    # 语义：pg_degraded 为 True 当且仅当「期望 PG 但退回了 MemorySaver」；
    # 未配置 DSN（dev/test）走 MemorySaver 属预期，不算降级。
    # checkpointer_kind 是生产上唯一能区分「真持久化 / 断点会丢」的信号，务必暴露到 /health。
    try:
        from app.graph.checkpointer import (
            checkpointer_kind,
            init_checkpointer,
            is_checkpointer_degraded,
        )

        app.state.checkpointer = await init_checkpointer()
        # 必须用 checkpointer_kind()（按 saver 实际类型判定），不能用
        # is_checkpointer_degraded() 取反——「未配置 DSN」时后者为 False（表示
        # 这不是降级），取反会误报 postgres，让监控以为断点已持久化。
        app.state.checkpointer_kind = checkpointer_kind()
        app.state.pg_degraded = is_checkpointer_degraded()
    except Exception:  # noqa: BLE001 — 降级启动，不阻断服务
        app.state.checkpointer = None
        app.state.checkpointer_kind = "memory"
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
    maintenance_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await maintenance_task
    # 溢出补偿循环（consumer 旁）：取消时其 finally 会把在途 cid 重塞回 Redis 队列。
    # 须在 MCP/checkpointer 关闭前完成（循环可能在 judge_async 中，需先释放）。
    if deferred_task is not None:
        deferred_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await deferred_task
    if eventbus_task is not None:
        eventbus_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await eventbus_task
        # 与 subscriber 内 complaint_eventbus_subscriber_stopped 互补（lifespan 侧可观测）
        import structlog

        structlog.get_logger().info(
            "complaint_eventbus_subscriber_shutdown",
            cancelled=eventbus_task.cancelled() or eventbus_task.done(),
        )
    # 取消在途排队研判任务：其 finally 仍会释放并发槽与幂等锁
    for task in list(_running_queued):
        task.cancel()
    for task in list(_running_queued):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _unwire_resilience_hooks()
    # 关闭 MCP client 持有的共享 HTTP 连接池（2.5 连接复用）。异常 suppress 不阻断关停。
    try:
        from app.api.deps import get_mcp_client

        await get_mcp_client().aclose()
    except Exception:  # noqa: BLE001 — 关闭失败不阻断 shutdown
        pass
    # 关闭 checkpointer 连接池。必须在在途排队任务 await 完之后（上方 239-243），
    # 否则正在执行的图会被抽掉连接。
    try:
        from app.graph.checkpointer import shutdown_checkpointer

        await shutdown_checkpointer()
    except Exception:  # noqa: BLE001 — 关池失败不阻断 shutdown
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
# 统一 Prometheus 指标：必须在所有 add_middleware 之后调用，
# 确保 MetricsMiddleware 处于最外层（Metrics→Trace→CORS→路由）；
# 同时注册无需鉴权的 GET /metrics（原生 exposition，不包统一信封）。
mount_metrics(app, service_name="complaint-service")
register_exception_handlers(app)

# 健康检查：同时提供 /health 与 /api/v1/health
app.include_router(health_router, prefix="/api/v1")
app.include_router(health_router)

# 业务路由（前缀 /api/v1）
# 挂载顺序：stats → mode → complaints（固定路径 /stats /mode 先于 /{id}，防被吞）
app.include_router(stats_router, prefix="/api/v1")
app.include_router(mode_router, prefix="/api/v1")
app.include_router(complaints_router, prefix="/api/v1")
