"""MCP Gateway FastAPI 入口。

Batch 1 脚手架：统一日志 / trace / 异常处理 + /health 管理端点。
- 1.4 挂载 MCP SDK server（/mcp，遵循 MCP 规范原始格式，不包装统一信封）；
- 1.5 挂载 Prometheus /metrics（原生 exposition 格式，不包装统一信封）；
- /health 等 JSON 管理端点使用统一信封 {"code","message","data","trace_id"}。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from app import mcp_server
from app.auth import MCPAuthMiddleware
from app.config import settings
from app.rate_limit import MCPRateLimitMiddleware
from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware, get_trace_id

setup_logging(env=settings.ENV, level=settings.LOG_LEVEL)

# 基础指标：总请求数（按 method/path/status）+ 活跃连接数（在途请求）。
# path 取值为有界集合（/health /metrics /mcp），无高基数风险。
REQUESTS_TOTAL = Counter(
    "mcp_gateway_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
ACTIVE_CONNECTIONS = Gauge("mcp_gateway_active_connections", "In-flight HTTP requests")


class MetricsMiddleware:
    """纯 ASGI 指标采集：不用 BaseHTTPMiddleware（避免缓冲破坏 /mcp 的 SSE 流）。"""

    def __init__(self, app) -> None:
        self.asgi_app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return
        status_holder = {"status": 500}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        ACTIVE_CONNECTIONS.inc()
        try:
            await self.asgi_app(scope, receive, send_wrapper)
        finally:
            ACTIVE_CONNECTIONS.dec()
            REQUESTS_TOTAL.labels(scope["method"], scope["path"], str(status_holder["status"])).inc()


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None]:
    # MCP streamable-http session manager 必须随应用启停（挂载子应用的 lifespan 不会自动执行）
    async with mcp_server.mcp.session_manager.run():
        # Batch 2/3：下游 HTTP 连接池在此初始化/清理
        yield


app = FastAPI(title="mcp-gateway", version="0.1.0", lifespan=lifespan)
# 中间件顺序（后添加 = 外层）：Metrics → Trace → Auth → RateLimit → 路由。
# Auth 在 Trace 内层以便 401 携带 trace_id，且在 RateLimit 外层——
# 未认证请求先被拒绝，不消耗 per-server 限流配额（/mcp 与 /metrics 同保护）。
# RateLimit 置于 Trace 内层以便事件携带 trace_id；Metrics 最外层覆盖所有请求（含挂载的 MCP 子应用）。
app.add_middleware(MCPRateLimitMiddleware)
app.add_middleware(MCPAuthMiddleware)
app.add_middleware(TraceMiddleware)
app.add_middleware(MetricsMiddleware)
register_exception_handlers(app)


@app.get("/health")
async def health() -> dict:
    """健康检查：统一信封，不做下游探活，不打业务日志（健康检查不得干扰业务）。"""
    return {
        "code": "0",
        "message": "ok",
        "data": {"status": "healthy", "service": "mcp-gateway"},
        "trace_id": get_trace_id(),
    }


# Prometheus /metrics：原生 exposition 格式（text/plain），SHALL NOT 包装统一信封
# （信封会破坏 Prometheus 抓取，spec「网关信封一致性」明确豁免）。
# ENABLE_AUTH=true 时由 MCPAuthMiddleware 要求 Bearer 凭证（Prometheus 抓取端
# 配置 bearer_token）；默认关闭时直接可达。
# 经 generate_latest 输出而非 mount make_asgi_app：裸 ASGI 函数会被 Route 误包装成 request 处理器。
@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# MCP 协议端点：挂载必须放在所有路由之后——根路径 Mount 按
# 注册顺序匹配，放在前面会抢占 /health 等管理端点。子应用内部路由为
# /mcp（streamable-http），遵循 MCP 规范原始格式，SHALL NOT 包装统一信封。
app.mount("/", mcp_server.mcp.streamable_http_app())
