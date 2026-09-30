"""网关可靠性编排层（Batch 3，任务 4.1/4.2/4.5）。

设计（design D3 + spec platform/mcp-reliability）：
- 可靠性原语复用 common 零件，本网关内不重写熔断/重试/超时/限流实现；
- 编排入口委托 `common.mcp.reliability.call_tool`（既有语义冻结）：
  per-server 熔断（key={svc}:mcp_degraded:{server}，fail_max=5，reset_timeout=30s）、
  total=30s 硬超时、错误分级（超时/权限拒绝不重试，业务错重试 3 次指数退避）；
- 网关侧只做适配：启动时 set_default_service(SERVICE_NAME) + set_mcp_transport 注入
  工具路由分发；调用上下文（trace_id）经 payload 保留键透传，不为 per-call 上下文
  重注册全局 transport（与在途调用竞态）。

错误分级映射（网关侧适配，对齐 common.mcp.reliability 原语）：
- 桥/下游单次超时与 5xx（MCPTimeoutError/MCPError）→ 业务可重试错：重试至多 3 次，
  指数退避（0.5s 起）；耗尽计 1 次熔断失败，再由路由 fallback 兜默认（空列表/默认值），
  图继续判不抛异常转人审（spec mcp-gateway「下游 5xx 返回空列表」）；
- 整次调用超 MCP total=30s（外层 wait_for 触发的 asyncio.TimeoutError）→ 不重试，
  计熔断失败 + mark_degraded_flag 标降级，调用方收到类型化 MCPTimeoutError；
- 权限拒绝（MCPPermissionDeniedError → PermissionError）→ 不重试、不计失败、直接终止；
- 熔断 open → MCPCircuitOpenError 类型化上抛，不触达下游。

超时层级（任务 4.5）：MCP total=30s（common.resilience.timeout.MCP_TOTAL_TIMEOUT）
< LLM invoke_llm total=120s（LLM_TOTAL_TIMEOUT），挂死的 MCP 调用先于编排它的
LLM 调用超时暴露。
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from common.llm.reliability import set_default_service
from common.mcp.reliability import (
    MCPCircuitOpenError,
    call_tool as reliability_call_tool,
    set_mcp_transport,
)
from common.resilience.timeout import MCP_TOTAL_TIMEOUT, Timeout

from app import audit
from app.config import settings
from app.mcp_errors import MCPError, MCPPermissionDeniedError, MCPTimeoutError

logger = structlog.get_logger()

# 调用上下文透传保留键（对齐 complaint-service MCPClient 的 _CTX_KEY 约定）
_CTX_KEY = "__ctx__"


async def _gateway_transport(
    server: str, tool: str, payload: dict[str, Any], timeout_seconds: float  # noqa: ARG001 — 整次硬上限由 reliability 外层 wait_for(total) 权威控制
) -> Any:
    """reliability 层 transport 适配器：分发到工具路由表 raw handler。

    timeout_seconds 为 reliability 传入的 total 预算（30s）；各桥内部另有
    per-attempt 超时（BRIDGE_HTTP_TIMEOUT/RAG_SEARCH_TIMEOUT/_CALL_TIMEOUT），
    整次调用硬上限由 reliability 外层 wait_for(total) 权威控制。

    异常映射：MCPPermissionDeniedError → PermissionError（不重试不计失败）；
    MCPError/MCPTimeoutError 透传（业务可重试错，reliability 内部重试 3 次）。
    """
    from app.tools import TOOL_ROUTES  # 延迟导入避免循环（tools 加载依赖 config/qc client）

    payload = dict(payload) if isinstance(payload, dict) else {}
    ctx = payload.pop(_CTX_KEY, None) or {}
    route = TOOL_ROUTES.get(server, {}).get(tool)
    if route is None:
        # dispatch 前置校验后不可达；防御性报错，SHALL NOT 静默转发
        raise MCPError(server, tool, "tool route not found")
    try:
        return await route.handler(payload, trace_id=str(ctx.get("trace_id") or ""))
    except MCPPermissionDeniedError as e:
        raise PermissionError(str(e)) from e


async def call_tool_reliable(
    server: str,
    tool: str,
    route: Any,
    params: dict[str, Any],
    *,
    trace_id: str = "",
    session_id: str = "",
    timeout: Timeout | None = None,
) -> Any:
    """dispatch 的可靠性包装：熔断检查 → 错误分级 → 超时/容错出口。

    - route: app.tools.ToolRoute（handler + fallback）；
    - timeout 可注入缩短（测试用），缺省 Timeout(total=MCP_TOTAL_TIMEOUT=30s)；
    - 业务错重试耗尽：熔断已计失败，调 route.fallback 返空列表/默认值（不抛异常）；
      同时经 audit.set_fallback_error 回传错误描述，dispatch 据此记 status=error；
    - 超时/权限拒绝/熔断 open：类型化上抛（MCPTimeoutError / MCPPermissionDeniedError /
      MCPCircuitOpenError），由 FastMCP 转为 MCP isError 结果。
    """
    effective_timeout = timeout or Timeout(total=MCP_TOTAL_TIMEOUT)
    payload = dict(params) if isinstance(params, dict) else {}
    payload[_CTX_KEY] = {"trace_id": trace_id, "session_id": session_id}
    try:
        return await reliability_call_tool(
            server,
            tool,
            payload,
            timeout=effective_timeout,
            # breaker_key 缺省自动派生 {svc}:mcp_degraded:{server}（svc=SERVICE_NAME=csmp）
            context={"trace_id": trace_id, "node": f"mcp:{server}"},
        )
    except MCPCircuitOpenError:
        # 熔断 open：快速拒绝，不触达 server（任务 4.1 验收语义）
        raise
    except PermissionError as e:
        # 权限拒绝不会自愈：不重试、不计熔断失败，直接终止
        logger.warning("mcp_permission_denied", server=server, tool=tool, trace_id=trace_id)
        raise MCPPermissionDeniedError(server, tool, str(e)) from e
    except asyncio.TimeoutError as e:
        # 整次调用超 total：熔断已计失败 + degraded 已置位（reliability 层完成）
        logger.warning(
            "mcp_total_timeout",
            server=server,
            tool=tool,
            total=effective_timeout.total,
            trace_id=trace_id,
        )
        raise MCPTimeoutError(
            server, tool, f"timeout after {effective_timeout.total}s"
        ) from e
    except Exception as e:  # noqa: BLE001 — 业务错重试耗尽：容错兜默认，图继续判
        logger.warning(
            "mcp_call_failed_fallback",
            server=server,
            tool=tool,
            error=str(e)[:300],
            trace_id=trace_id,
        )
        # 审计如实记录底层失败（调用方仍收到兜底值，图继续判不转人审）
        audit.set_fallback_error(f"business_error: {type(e).__name__}: {e}"[:300])
        return await route.fallback(params, trace_id=trace_id, error=e)


def init_reliability() -> None:
    """启动接线：服务名 + 工具路由 transport 注入（进程内仅执行一次）。

    SERVICE_NAME 为空时 fail-fast（resolve_service_name 不允许无前缀全局 key）。
    """
    svc = (settings.SERVICE_NAME or "").strip()
    if not svc:
        raise RuntimeError(
            "MCP_GATEWAY_SERVICE_NAME 未配置：熔断/限流 key 需要 {svc} 前缀"
        )
    set_default_service(svc)
    set_mcp_transport(_gateway_transport)
    logger.info("gateway_reliability_ready", service=svc, mcp_total=MCP_TOTAL_TIMEOUT)


init_reliability()
