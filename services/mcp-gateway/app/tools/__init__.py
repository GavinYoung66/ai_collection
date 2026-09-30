"""工具静态注册表：4 个 server 的 schema + 处理器装配（任务 2.5 路由层数据源）。

- schema 静态注册，与 complaint-service 现有桥的工具契约对齐（不做动态发现）
- call_qc 混合实现（design D1）：
  query_call_asr → 内嵌（HTTP 调 data-platform qc-source）
  query_qc_violations / query_qc_alerts → MCP client 调 qc-service /mcp
- 处理器签名：(params, *, trace_id) → list | dict；Batch 3 起下游故障抛
  MCPError/MCPTimeoutError（由可靠性层分级重试），查无数据仍就地返空/默认
- Batch 3：每条路由挂 fallback(params, *, trace_id, error)——业务错重试耗尽后
  由可靠性层调用，兜空列表/默认值（spec mcp-gateway「下游 5xx 返回空列表或默认值」）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.config import settings
from app.qc_mcp_client import call_qc_remote
from app.tools.business_ticket import BusinessTicketToolset, _default_case_fixture
from app.tools.call_qc_asr import CallQcAsrToolset
from app.tools.data_platform import DataPlatformToolset
from app.tools.rag_kb import RagKbToolset

# (params, *, trace_id) → list | dict
ToolFn = Callable[..., Awaitable[Any]]
# (params, *, trace_id, error) → 容错默认值（list | dict）
FallbackFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolRoute:
    """一条工具路由：raw 处理器 + 容错兜底。

    handler 抛出的业务错经可靠性层重试耗尽后，由 fallback 产出调用方可见的
    默认值；fallback 自身 SHALL NOT 抛异常（图继续判，不转人工审核）。
    """

    handler: ToolFn
    fallback: FallbackFn

_CASE_ID_SCHEMA = {
    "type": "object",
    "properties": {"case_id": {"type": "string", "description": "案件 ID"}},
    "required": ["case_id"],
}

# 静态工具 schema（与源桥 TOOL 定义一致；inputSchema 按调用方实参收敛）。
# 注意：mcp 1.30 FastMCP 对 list 返回值按「每元素一个 TextContent」编码，客户端无法
# 区分 list/dict，须靠静态 schema 还原——complaint-service real_transport.py 的
# `_LIST_SHAPE_TOOLS` 静态镜像了「返回 list 的工具」集合。凡新增返回 list 的工具，
# 必须同步更新该集合，否则客户端会把 list 误解析为 dict/空。
TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "server": "rag_kb",
        "name": "search_regulations",
        "description": "检索监管法规/合规规则知识库",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索 query（投诉正文摘要等）"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "server": "rag_kb",
        "name": "search_response_templates",
        "description": "检索应答话术模板知识库",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索 query"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "server": "business_ticket",
        "name": "query_case_info",
        "description": "查询案件快照信息（逾期金额/天数/客户/产品等）",
        "inputSchema": dict(_CASE_ID_SCHEMA),
    },
    {
        "server": "business_ticket",
        "name": "query_history_tickets",
        "description": "查询历史工单（本闭环无历史表，恒返空列表）",
        "inputSchema": dict(_CASE_ID_SCHEMA),
    },
    {
        "server": "business_ticket",
        "name": "query_history_complaints",
        "description": "查询历史投诉（本闭环无历史表，恒返空列表）",
        "inputSchema": dict(_CASE_ID_SCHEMA),
    },
    {
        "server": "data_platform",
        "name": "write_session",
        "description": "回写研判会话结果到快照投诉列",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "研判会话 ID"},
                "task_status": {"type": "string"},
                "verdict": {"type": "string"},
                "case_id": {"type": "string", "description": "可选：直带案件 ID（跳过反查）"},
                "complaint_id": {"type": "string", "description": "可选：直带投诉 ID（跳过反查）"},
            },
            "required": ["session_id"],
        },
    },
    {
        "server": "data_platform",
        "name": "write_human_review_truth",
        "description": "回写人工复核真值到快照投诉列",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "研判会话 ID"},
                "task_status": {"type": "string"},
                "verdict": {"type": "string"},
                "case_id": {"type": "string", "description": "可选：直带案件 ID（跳过反查）"},
                "complaint_id": {"type": "string", "description": "可选：直带投诉 ID（跳过反查）"},
            },
            "required": ["session_id"],
        },
    },
    {
        "server": "call_qc",
        "name": "query_call_asr",
        "description": "查询通话 ASR 逐句（内嵌网关，HTTP 调 data-platform qc-source）",
        "inputSchema": dict(_CASE_ID_SCHEMA),
    },
    {
        "server": "call_qc",
        "name": "query_qc_violations",
        "description": "查询质检违规明细（MCP 协议调 qc-service）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "session_id": {"type": "string", "description": "通话会话 ID（缺省经网关快照反查）"},
            },
            "required": ["case_id"],
        },
    },
    {
        "server": "call_qc",
        "name": "query_qc_alerts",
        "description": "查询质检高危告警（MCP 协议调 qc-service）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "session_id": {"type": "string", "description": "通话会话 ID（缺省经网关快照反查）"},
            },
            "required": ["case_id"],
        },
    },
)


async def _empty_list_fallback(
    params: dict, *, trace_id: str = "", error: BaseException | None = None  # noqa: ARG001 — 兜底签名统一
) -> list:
    """通用容错兜底：业务错重试耗尽后返空列表（rag_kb/call_qc/历史查询）。"""
    return []


async def _case_fixture_fallback(
    params: dict, *, trace_id: str = "", error: BaseException | None = None  # noqa: ARG001 — 兜底签名统一
) -> dict:
    """query_case_info 容错兜底：默认 fixture（零值字段，不伪造数据）。"""
    return _default_case_fixture(str((params or {}).get("case_id") or ""))


def build_tool_routes() -> dict[str, dict[str, ToolRoute]]:
    """装配 server → tool → ToolRoute 路由表（供 mcp_server.dispatch 路由）。"""
    rag_kb = RagKbToolset(settings.RAG_SERVICE_URL, timeout=settings.RAG_SEARCH_TIMEOUT)
    business_ticket = BusinessTicketToolset(settings.DATA_PLATFORM_URL)
    data_platform = DataPlatformToolset(settings.DATA_PLATFORM_URL)
    call_qc_asr = CallQcAsrToolset(settings.DATA_PLATFORM_URL)

    async def _remote(tool: str, params: dict, *, trace_id: str = "") -> list[dict[str, Any]]:
        return await call_qc_remote(tool, params, trace_id=trace_id)

    return {
        "rag_kb": {
            "search_regulations": ToolRoute(
                handler=lambda params, *, trace_id="": rag_kb.call_tool(
                    "search_regulations", params, trace_id=trace_id
                ),
                fallback=_empty_list_fallback,
            ),
            "search_response_templates": ToolRoute(
                handler=lambda params, *, trace_id="": rag_kb.call_tool(
                    "search_response_templates", params, trace_id=trace_id
                ),
                fallback=_empty_list_fallback,
            ),
        },
        "business_ticket": {
            "query_case_info": ToolRoute(
                handler=lambda params, *, trace_id="": business_ticket.call_tool(
                    "query_case_info", params, trace_id=trace_id
                ),
                fallback=_case_fixture_fallback,
            ),
            "query_history_tickets": ToolRoute(
                handler=lambda params, *, trace_id="": business_ticket.call_tool(
                    "query_history_tickets", params, trace_id=trace_id
                ),
                fallback=_empty_list_fallback,
            ),
            "query_history_complaints": ToolRoute(
                handler=lambda params, *, trace_id="": business_ticket.call_tool(
                    "query_history_complaints", params, trace_id=trace_id
                ),
                fallback=_empty_list_fallback,
            ),
        },
        "data_platform": {
            "write_session": ToolRoute(
                handler=lambda params, *, trace_id="": data_platform.call_tool(
                    "write_session", params, trace_id=trace_id
                ),
                fallback=lambda params, *, trace_id="", error=None: data_platform.fallback(
                    "write_session", params, trace_id=trace_id, error=error
                ),
            ),
            "write_human_review_truth": ToolRoute(
                handler=lambda params, *, trace_id="": data_platform.call_tool(
                    "write_human_review_truth", params, trace_id=trace_id
                ),
                fallback=lambda params, *, trace_id="", error=None: data_platform.fallback(
                    "write_human_review_truth", params, trace_id=trace_id, error=error
                ),
            ),
        },
        "call_qc": {
            "query_call_asr": ToolRoute(
                handler=lambda params, *, trace_id="": call_qc_asr.call_tool(
                    "query_call_asr", params, trace_id=trace_id
                ),
                fallback=_empty_list_fallback,
            ),
            "query_qc_violations": ToolRoute(
                handler=lambda params, *, trace_id="": _remote(
                    "query_qc_violations", params, trace_id=trace_id
                ),
                fallback=_empty_list_fallback,
            ),
            "query_qc_alerts": ToolRoute(
                handler=lambda params, *, trace_id="": _remote(
                    "query_qc_alerts", params, trace_id=trace_id
                ),
                fallback=_empty_list_fallback,
            ),
        },
    }


# 路由表模块级单例：网关进程内唯一，reliability transport 适配器与 dispatch 共用
TOOL_ROUTES = build_tool_routes()
