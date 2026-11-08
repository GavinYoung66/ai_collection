"""Complaint Service FastAPI 入口。

批次1 脚手架：注册中间件/异常处理/路由，预留 PG/MCP 生命周期初始化（降级启动）。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.observability.metrics import mount_metrics
from common.trace.middleware import TraceMiddleware
from app.api.auth import register_auth_handlers
from app.api.health import router as health_router
from app.api.routes.complaints import router as complaints_router
from app.api.routes.mode import router as mode_router
from app.api.routes.stats import router as stats_router
from app.bootstrap import wire_resilience_hooks
from app.bootstrap import unwire_resilience_hooks as _unwire_resilience_hooks
from app.config import settings

setup_logging(env=getattr(settings, "ENV", "dev"), level=getattr(settings, "LOG_LEVEL", None))

logger = structlog.get_logger()


async def _probe_mcp_gateway() -> bool:
    """standalone 且非 Mock 时探活 MCP_GATEWAY_URL；失败返回 False（标记 mcp_degraded）。

    inline / Mock：不发起外网探活，视为可达。

    T43：原实现用阻塞 `urllib.request.urlopen`（最长 2×2s）直接在异步 lifespan
    中调用，会冻结事件循环、劣化并发健康检查响应；改为 `httpx.AsyncClient`
    异步探测（每路径 2s 超时，语义与原实现一致：/health → 根路径，2xx 即可达）。
    """
    mode = getattr(settings, "MCP_GATEWAY_MODE", "inline")
    if mode != "standalone":
        return True
    if getattr(settings, "USE_MOCK_MCP", True):
        return True
    gateway_url = (getattr(settings, "MCP_GATEWAY_URL", "") or "").rstrip("/")
    if not gateway_url:
        return False

    for path in ("/health", ""):
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{gateway_url}{path}")
            if 200 <= resp.status_code < 300:
                return True
        except httpx.HTTPError:
            continue
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """startup：真路径 MCP 配置 fail-fast；PG/MCP 探活；shutdown 清理。

    - USE_MOCK_MCP=false 且缺 RAG_SERVICE_URL：启动即失败（DEBT-04）
    - Mock 默认路径：不要求 rag-service
    """
    # ── startup ──
    # T03：JWT/AES 密钥为默认占位值时立即失败（生产必须由 KMS 注入）
    from common.config.settings import validate_required_secrets

    validate_required_secrets(settings)

    # 链路追踪兜底：统一开关关闭时清除 LangSmith/LangChain SDK 环境变量，
    # 防止 LangGraph 等 SDK 读 os.environ 绕过闸门自动上报（与 rag-service 对齐）。
    from common.trace.langfuse_gate import configure_langfuse_env
    from common.trace.langsmith_gate import configure_langsmith_env

    configure_langsmith_env(settings)
    configure_langfuse_env(settings)

    # T02：认证模式启动日志（生产强制开启；本地关闭时显著告警）
    from common.security.rbac import log_auth_mode

    log_auth_mode(settings, "complaint-service")

    # G7：韧性事件（csmp.{component}.{action}）统一路由到本服务 publisher
    wire_resilience_hooks()

    # T25：排队消费 / 周期维护 / 溢出补偿 / EventBus 订阅已剥离到独立
    # worker 进程（app.worker）；HTTP 进程不再创建任何常驻循环。

    app.state.pg_degraded = False
    app.state.redis_degraded = False
    app.state.mcp_degraded = False
    app.state.mode_redis = None

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
    except Exception as exc:  # noqa: BLE001 — 降级启动，不阻断服务
        # T71：checkpointer 降级到 memory——持久化语义丢失，值得暴露
        logger.warning(
            "checkpointer_init_degraded_to_memory",
            error=str(exc),
            exc_info=True,
        )
        app.state.checkpointer = None
        app.state.checkpointer_kind = "memory"
        app.state.pg_degraded = True

    # T112：模式状态共享 Redis 装配（spec complaint-service/mode「模式状态多副本一致」）。
    # ModeService 为同步 API，注入同步 Redis 客户端；ping 探活走 to_thread（事件循环
    # 纪律）；失败降级保留进程内实现（与 checkpointer 降级形态一致），redis_degraded
    # 由 /health 如实暴露。管理操作低频，运行期同步短超时 IO 可接受（状态行留痕）。
    from app.service.mode_service import build_shared_redis_client, mode_service

    mode_redis = None
    try:
        mode_redis = build_shared_redis_client()
        await asyncio.to_thread(mode_redis.ping)
        mode_service.set_redis_client(mode_redis)
        logger.info("mode_redis_assembled")
    except Exception as exc:  # noqa: BLE001 — 降级启动，不阻断服务
        # T71/T112：Redis 不可达按降级处理——模式状态暂存进程内存（单副本语义），
        # health 标记 degraded，值得暴露
        logger.warning(
            "mode_redis_assembly_degraded_to_memory",
            error=str(exc),
            exc_info=True,
        )
        # T112：ping 失败时客户端已创建，必须关闭释放连接池，避免启动期泄漏。
        if mode_redis is not None:
            try:
                await asyncio.to_thread(mode_redis.close)
            except Exception:  # noqa: BLE001 — 降级清理失败不阻断启动
                logger.warning("mode_redis_close_failed_on_degrade", exc_info=True)
        mode_redis = None
        app.state.redis_degraded = True
    app.state.mode_redis = mode_redis

    # MCP：standalone 且非 Mock 时探活网关，避免 health mcp 假绿
    # T43：异步探测，不阻塞事件循环（并发 /health 响应不因探测劣化）
    try:
        if not await _probe_mcp_gateway():
            app.state.mcp_degraded = True
    except Exception:  # noqa: BLE001
        # T71：MCP 探活异常按降级处理——health 假绿风险，值得暴露
        logger.warning("mcp_probe_failed_mark_degraded", exc_info=True)
        app.state.mcp_degraded = True

    yield

    # ── shutdown ──
    # T25：常驻循环与 EventBus 订阅由 app.worker 进程负责关停；
    # HTTP 进程只需回收自身装配的资源。
    _unwire_resilience_hooks()
    # 关闭 MCP client 持有的共享 HTTP 连接池（2.5 连接复用）。异常 suppress 不阻断关停。
    try:
        from app.api.deps import get_mcp_client

        await get_mcp_client().aclose()
    except Exception:  # noqa: BLE001 — 关闭失败不阻断 shutdown
        logger.warning("mcp_client_aclose_failed", exc_info=True)
    # 关闭 checkpointer 连接池（worker 进程关停时会先停掉在途任务）。
    try:
        from app.graph.checkpointer import shutdown_checkpointer

        await shutdown_checkpointer()
    except Exception:  # noqa: BLE001 — 关池失败不阻断 shutdown
        logger.warning("checkpointer_shutdown_failed", exc_info=True)
    # T57：关闭共享 LLM HTTP client（transport 按 loop 隔离，旧 loop 残留
    # 由 aclose 内部防御性 best-effort 关闭；失败不阻断关停）。
    try:
        from common.llm.transport import aclose_shared_client

        await aclose_shared_client()
    except Exception:  # noqa: BLE001 — 关闭失败不阻断 shutdown
        logger.warning("llm_shared_client_aclose_failed", exc_info=True)
    # T112：关闭模式状态共享 Redis 连接（装配失败降级时为 None，跳过）。
    if getattr(app.state, "mode_redis", None) is not None:
        try:
            await asyncio.to_thread(app.state.mode_redis.close)
        except Exception:  # noqa: BLE001 — 关闭失败不阻断 shutdown
            logger.warning("mode_redis_close_failed", exc_info=True)
    # W4：关闭 resilience 共享 Redis 连接池（rate_limit/idempotency 共用）。
    try:
        from common.resilience._redis import aclose_redis

        await aclose_redis()
    except Exception:  # noqa: BLE001 — 关池失败不阻断 shutdown
        logger.warning("resilience_redis_aclose_failed", exc_info=True)
    # T58：释放 SQLAlchemy engine 连接池（PG 连接归零）。
    try:
        from common.db.session import engine

        await engine.dispose()
    except Exception:  # noqa: BLE001 — 关池失败不阻断 shutdown
        logger.warning("engine_dispose_failed", exc_info=True)


app = FastAPI(title="complaint-service", version="1.0.0", lifespan=lifespan)
# 跨域消费者（本地人审静态页）已移除；真实前端经网关同源访问，无需 CORS 中间件。
app.add_middleware(TraceMiddleware)
# 统一 Prometheus 指标：必须在所有 add_middleware 之后调用，
# 确保 MetricsMiddleware 处于最外层（Metrics→Trace→路由）；
# 同时注册无需鉴权的 GET /metrics（原生 exposition，不包统一信封）。
mount_metrics(app, service_name="complaint-service")
register_exception_handlers(app)
register_auth_handlers(app)

# 健康检查：同时提供 /health 与 /api/v1/health
app.include_router(health_router, prefix="/api/v1")
app.include_router(health_router)

# 业务路由（前缀 /api/v1）
# 挂载顺序：stats → mode → complaints（固定路径 /stats /mode 先于 /{id}，防被吞）
app.include_router(stats_router, prefix="/api/v1")
app.include_router(mode_router, prefix="/api/v1")
app.include_router(complaints_router, prefix="/api/v1")
