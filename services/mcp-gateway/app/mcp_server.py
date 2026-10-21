"""MCP server 基座（FastMCP 高层 API，mcp 1.x）。

Batch 2（任务 2.1-2.6 / 3.1-3.3）：工具 schema 静态注册（app.tools.TOOL_SPECS），
dispatch 按 server → tool 两级路由（任务 2.5）；transport 采用 streamable-http，
路径 /mcp，不包装统一信封。

Batch 4（任务 5.1-5.4）：每次入站调用（tools/list + tools/call）记 mcp_calls
审计；tools/call 出口做 PII 递归脱敏（design D4 网关第一道）。

call_qc 为混合实现（design D1），同一 server 下不同 tool 分流到不同 transport：
- call_qc.query_call_asr → 内嵌网关（HTTP 调 data-platform qc-source，任务 2.6）
- call_qc.query_qc_violations / query_qc_alerts → MCP client 调 qc-service /mcp（任务 3.2）

线网工具命名：``{server}.{tool}``（如 ``rag_kb.search_regulations``），
与 complaint-service 图节点 mcp.call(server, tool, …) 的两段式一一对应。
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from common.mcp.reliability import MCPCircuitOpenError
from common.trace.middleware import get_trace_id

from app import audit, pii
from app.reliability import call_tool_reliable
from app.tools import TOOL_ROUTES, TOOL_SPECS

logger = structlog.get_logger()

# 四个后端 server（spec：list_tools SHALL 聚合 rag_kb / business_ticket / call_qc / data_platform）
KNOWN_SERVERS = ("rag_kb", "business_ticket", "data_platform", "call_qc")


class UnknownServerError(Exception):
    """未知 server 名：明确报错，SHALL NOT 转发。"""


class UnknownToolError(Exception):
    """已知 server 下未注册的 tool：明确报错。"""


# server 名 -> {tool 名 -> ToolRoute}；静态注册（app.tools.TOOL_ROUTES 模块级单例）


def _audit_error(exc: BaseException) -> str:
    """异常 → mcp_calls.error 分类描述（超时/熔断/权限/未知工具/未分类）。"""
    if isinstance(exc, MCPCircuitOpenError):
        return "circuit_open"
    # MCPTimeoutError / MCPPermissionDeniedError 来自 app.mcp_errors
    if type(exc).__name__ == "MCPTimeoutError":
        return "timeout"
    if type(exc).__name__ == "MCPPermissionDeniedError":
        return "permission_denied"
    if isinstance(exc, (UnknownServerError, UnknownToolError)):
        return f"unknown_route: {exc}"
    return f"unexpected: {type(exc).__name__}: {exc}"


async def dispatch(
    server: str, tool: str, params: dict, *, trace_id: str = "", session_id: str = ""
) -> Any:
    """call_tool 路由：server 名 → tool 名 两级分发（任务 2.5）。

    未知 server 抛 UnknownServerError，SHALL NOT 转发到任何下游。
    Batch 3：路由校验前置（未知 server/tool 不进可靠性层），命中后委托
    call_tool_reliable 完成 per-server 熔断 + 30s 超时 + 错误分级 + 容错兜底。
    Batch 4：本方法是 tools/call 的唯一审计点——成功/失败双路均记录
    mcp_calls 审计（任务 5.1/5.2）；出口 PII 递归脱敏在返回前完成（任务 5.3/5.4）。
    """
    # 优先取调用方经 _meta 透传的 trace_id；缺省回退 HTTP X-Trace-Id（全链路串联）
    trace_id = trace_id or get_trace_id()
    start = time.monotonic()
    # 只统计本次调用产生的 fallback 信号，杜绝跨调用串扰
    audit.reset_fallback_error()
    try:
        if server not in KNOWN_SERVERS:
            raise UnknownServerError(f"unknown server: {server!r}, known={list(KNOWN_SERVERS)}")
        route = TOOL_ROUTES.get(server, {}).get(tool)
        if route is None:
            raise UnknownToolError(f"unknown tool {tool!r} for server {server!r}")
        result = await call_tool_reliable(
            server, tool, route, params, trace_id=trace_id, session_id=session_id
        )
    except Exception as e:  # noqa: BLE001 — 失败路：记 status=error 审计后原样上抛
        audit.record_mcp_call(
            server,
            tool,
            params,
            trace_id=trace_id,
            session_id=session_id,
            start=start,
            error=_audit_error(e),
        )
        raise

    # 业务错重试耗尽但被容错兜底：调用方收到默认值，审计如实记 status=error
    fallback_error = audit.take_fallback_error()

    # 任务 5.3/5.4：出口 PII 递归脱敏；命中明文先报 mcp_pii_leak_sanitized 事件
    if pii.contains_pii(result):
        audit.report_pii_leak(server, tool, trace_id=trace_id)
        result = pii.sanitize(result)

    audit.record_mcp_call(
        server,
        tool,
        params,
        trace_id=trace_id,
        session_id=session_id,
        start=start,
        error=fallback_error,
    )
    return result


def _trace_id_from_ctx(ctx: Context | None) -> str:
    """从 MCP 请求 _meta 提取 trace_id（调用方经 params._meta 透传，容错缺省）。"""
    return _meta_value(ctx, ("trace_id", "traceId", "x-trace-id"))


def _session_id_from_ctx(ctx: Context | None) -> str:
    """从 MCP 请求 _meta 提取 session_id（业务会话透传，容错缺省）。"""
    return _meta_value(ctx, ("session_id", "sessionId", "x-session-id"))


def _meta_value(ctx: Context | None, keys: tuple[str, ...]) -> str:
    try:
        meta = ctx.request_context.meta if ctx and ctx.request_context else None
    except Exception:  # noqa: BLE001 — meta 提取失败不阻断工具调用
        return ""
    if meta is None:
        return ""
    for key in keys:
        value = getattr(meta, key, None)
        if isinstance(value, str) and value:
            return value
    return ""


class GatewayFastMCP(FastMCP):
    """FastMCP 子类：在协议层为 tools/list 增补 mcp_calls 审计（任务 5.1）。

    tools/call 的审计/脱敏在工具函数统一入口 dispatch 完成（需 server/tool
    两段信息与原始返回值，工具函数层是唯一同时具备三者的位置）。
    """

    async def list_tools(self) -> list:
        # 与 tools/call 一致：trace_id 优先取调用方 _meta，缺省回退 HTTP X-Trace-Id
        ctx = self.get_context()
        trace_id = _trace_id_from_ctx(ctx) or get_trace_id()
        start = time.monotonic()
        try:
            tools = await super().list_tools()
        except Exception as e:  # noqa: BLE001 — list_tools 失败也留审计
            audit.record_mcp_call(
                "",
                "list_tools",
                None,
                trace_id=trace_id,
                session_id="",
                start=start,
                error=f"list_tools: {type(e).__name__}: {e}",
            )
            raise
        audit.record_mcp_call(
            "",
            "list_tools",
            None,
            trace_id=trace_id,
            session_id="",
            start=start,
        )
        return tools


# stateless_http：无会话粘滞，便于多实例部署；streamable-http 路径保持默认 /mcp。
# DNS rebinding 防护：FastMCP 默认 enable_dns_rebinding_protection=True 且
# allowed_hosts=[]，docker 服务名（Host: mcp-gateway:9000）会被 _validate_host
# 判为非法 Host → 421 Misdirected Request。此处把服务名加入白名单（:* 通配端口）。
mcp = GatewayFastMCP(
    name="mcp-gateway",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "mcp-gateway:*",  # 覆盖 mcp-gateway:9000 及任意端口（SDK 支持 :* 通配）
            "localhost",
            "127.0.0.1",
            "::1",
        ],
    ),
)


def _register_tools() -> None:
    """按 TOOL_SPECS 静态注册 FastMCP 工具（名 = {server}.{tool}）。

    工具函数签名即 inputSchema 事实源；描述取自 TOOL_SPECS。
    """
    specs = {(s["server"], s["name"]): s for s in TOOL_SPECS}

    def _desc(server: str, tool: str) -> str:
        return str(specs[(server, tool)]["description"])

    # ---- rag_kb ----
    async def rag_kb_search_regulations(query: str, top_k: int = 5, ctx: Context = None) -> list:
        return await dispatch(
            "rag_kb", "search_regulations",
            {"query": query, "top_k": top_k}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    async def rag_kb_search_response_templates(query: str, top_k: int = 5, ctx: Context = None) -> list:
        return await dispatch(
            "rag_kb", "search_response_templates",
            {"query": query, "top_k": top_k}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    # ---- business_ticket ----
    async def business_ticket_query_case_info(case_id: str, ctx: Context = None) -> dict:
        return await dispatch(
            "business_ticket", "query_case_info",
            {"case_id": case_id}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    async def business_ticket_query_history_tickets(case_id: str, ctx: Context = None) -> list:
        return await dispatch(
            "business_ticket", "query_history_tickets",
            {"case_id": case_id}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    async def business_ticket_query_history_complaints(case_id: str, ctx: Context = None) -> list:
        return await dispatch(
            "business_ticket", "query_history_complaints",
            {"case_id": case_id}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    # ---- data_platform ----
    async def data_platform_write_session(
        session_id: str,
        task_status: str = "",
        verdict: str = "",
        case_id: str = "",
        complaint_id: str = "",
        ctx: Context = None,
    ) -> dict:
        return await dispatch(
            "data_platform", "write_session",
            {
                "session_id": session_id,
                "task_status": task_status,
                "verdict": verdict,
                "case_id": case_id,
                "complaint_id": complaint_id,
            },
            trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    async def data_platform_write_human_review_truth(
        session_id: str,
        task_status: str = "",
        verdict: str = "",
        case_id: str = "",
        complaint_id: str = "",
        ctx: Context = None,
    ) -> dict:
        return await dispatch(
            "data_platform", "write_human_review_truth",
            {
                "session_id": session_id,
                "task_status": task_status,
                "verdict": verdict,
                "case_id": case_id,
                "complaint_id": complaint_id,
            },
            trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    # ---- call_qc（混合实现）----
    async def call_qc_query_call_asr(case_id: str, ctx: Context = None) -> list:
        return await dispatch(
            "call_qc", "query_call_asr",
            {"case_id": case_id}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    async def call_qc_query_qc_violations(case_id: str, session_id: str = "", ctx: Context = None) -> list:
        return await dispatch(
            "call_qc", "query_qc_violations",
            {"case_id": case_id, "session_id": session_id}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    async def call_qc_query_qc_alerts(case_id: str, session_id: str = "", ctx: Context = None) -> list:
        return await dispatch(
            "call_qc", "query_qc_alerts",
            {"case_id": case_id, "session_id": session_id}, trace_id=_trace_id_from_ctx(ctx),
            session_id=_session_id_from_ctx(ctx),
        )

    registrations = (
        ("rag_kb", "search_regulations", rag_kb_search_regulations),
        ("rag_kb", "search_response_templates", rag_kb_search_response_templates),
        ("business_ticket", "query_case_info", business_ticket_query_case_info),
        ("business_ticket", "query_history_tickets", business_ticket_query_history_tickets),
        ("business_ticket", "query_history_complaints", business_ticket_query_history_complaints),
        ("data_platform", "write_session", data_platform_write_session),
        ("data_platform", "write_human_review_truth", data_platform_write_human_review_truth),
        ("call_qc", "query_call_asr", call_qc_query_call_asr),
        ("call_qc", "query_qc_violations", call_qc_query_qc_violations),
        ("call_qc", "query_qc_alerts", call_qc_query_qc_alerts),
    )
    for server, tool, fn in registrations:
        mcp.add_tool(fn, name=f"{server}.{tool}", description=_desc(server, tool))


_register_tools()
