"""MCP Gateway FastAPI 入口。

Batch 1 脚手架：统一日志 / trace / 异常处理 + /health 管理端点。
- 1.4 挂载 MCP SDK server（/mcp，遵循 MCP 规范原始格式，不包装统一信封）；
- 1.5 经 common.observability.metrics.mount_metrics 暴露 Prometheus /metrics
  （原生 exposition 格式，不包装统一信封；纯 ASGI 中间件不缓冲 /mcp SSE 流）；
- /health 等 JSON 管理端点使用统一信封 {"code","message","data","trace_id"}。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app import mcp_server
from app.auth import MCPAuthMiddleware
from app.config import settings
from app.rate_limit import MCPRateLimitMiddleware
from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.observability.metrics import mount_metrics
from common.trace.middleware import TraceMiddleware, get_trace_id

setup_logging(env=settings.ENV, level=settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None]:
    # MCP streamable-http session manager 必须随应用启停（挂载子应用的 lifespan 不会自动执行）
    async with mcp_server.mcp.session_manager.run():
        # Batch 2/3：下游 HTTP 连接池在此初始化/清理
        yield


app = FastAPI(title="mcp-gateway", version="0.1.0", lifespan=lifespan)
# 中间件顺序（后添加 = 外层）：Metrics → Trace → Auth → RateLimit → 路由。
# Auth 在 Trace 内层以便 401 携带 trace_id，且在 RateLimit 外层——
# 未认证请求先被拒绝，不消耗 per-server 限流配额（仅 /mcp 受保护；
# /metrics 为 Prometheus 拉取端点，已移出鉴权保护直通，见 auth.py）。
# RateLimit 置于 Trace 内层以便事件携带 trace_id；Metrics 最外层覆盖所有请求（含挂载的 MCP 子应用）。
app.add_middleware(MCPRateLimitMiddleware)
app.add_middleware(MCPAuthMiddleware)
app.add_middleware(TraceMiddleware)
# mount_metrics 内部最后加挂纯 ASGI MetricsMiddleware（保持最外层）并注册 GET /metrics；
# 指标统一为 http_requests_total{svc="mcp-gateway",...}，旧 mcp_gateway_* 服务前缀指标已废弃。
mount_metrics(app, service_name="mcp-gateway")
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


# Prometheus /metrics 已由 mount_metrics 注册：原生 exposition 格式（text/plain），
# SHALL NOT 包装统一信封（信封会破坏 Prometheus 抓取，spec「网关信封一致性」明确豁免）。

# MCP 协议端点：挂载必须放在所有路由之后——根路径 Mount 按
# 注册顺序匹配，放在前面会抢占 /health 等管理端点。子应用内部路由为
# /mcp（streamable-http），遵循 MCP 规范原始格式，SHALL NOT 包装统一信封。
app.mount("/", mcp_server.mcp.streamable_http_app())
