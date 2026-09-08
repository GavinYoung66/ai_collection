"""Complaint Service FastAPI 入口。

批次1 脚手架：注册中间件/异常处理/路由，预留 PG/MCP 生命周期初始化（降级启动）。
"""
from __future__ import annotations

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """startup：真路径 MCP 配置 fail-fast；PG/MCP 探活；shutdown 清理。

    - USE_MOCK_MCP=false 且缺 RAG_SERVICE_URL：启动即失败（DEBT-04）
    - Mock 默认路径：不要求 rag-service
    """
    # ── startup ──
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
