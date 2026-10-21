"""真路径 MCP transport：MCP SDK client → 独立 mcp-gateway（design D2）。

Batch 5（mcp-gateway-standalone）：
- ``USE_MOCK_MCP=false`` 时唯一真路径：streamable-http 连 ``{MCP_GATEWAY_URL}/mcp``
- 线网工具命名 ``{server}.{tool}``（网关 FastMCP 静态注册命名，见
  services/mcp-gateway/app/mcp_server.py），经 MCP 协议 call_tool 调网关，
  由网关负责路由到 rag-service / data-platform / qc-service
- trace_id / session_id 经 MCP call_tool 的 meta 透传（网关 _meta 提取）
- 未知 server 在客户端侧直接拒绝，不建连、SHALL NOT 转发
- 网关为 ``stateless_http``，每次调用短连接、无会话粘滞
- 进程内桥（rag_kb / business_ticket / call_qc / data_platform）已迁移网关，
  本模块 SHALL NOT import / 实例化任何桥，SHALL NOT 进程内 HTTP 直调下游

异常映射复用 app/mcp/errors.py，禁止第二套异常类：
- httpx 超时（含 ExceptionGroup 解包）→ MCPTimeoutError
- 401/403 → MCPPermissionDeniedError
- 其他传输/协议错、工具 isError → MCPError
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import settings
from app.mcp.errors import MCPError, MCPPermissionDeniedError, MCPTimeoutError
from common.llm.reliability import is_placeholder_secret

logger = structlog.get_logger()

# 网关聚合的 4 个后端 server（与 app.mcp.gateway.ALLOWED_SERVERS 一致）
KNOWN_SERVERS = frozenset({"business_ticket", "call_qc", "rag_kb", "data_platform"})

# 网关静态注册工具中返回 list 形状的 (server, tool)；其余已注册工具返回 dict。
# 事实源：services/mcp-gateway/app/mcp_server.py 注册的 10 个工具 + 各桥 fixture 形状。
# 必须静态声明的原因：mcp 1.30 FastMCP 对 list 返回值按「每元素一个 TextContent
# block」编码，单元素 list 与 dict 在线上形态不可区分，只能靠静态 schema 还原。
_LIST_SHAPE_TOOLS = frozenset({
    ("rag_kb", "search_regulations"),
    ("rag_kb", "search_response_templates"),
    ("business_ticket", "query_history_tickets"),
    ("business_ticket", "query_history_complaints"),
    ("call_qc", "query_call_asr"),
    ("call_qc", "query_qc_violations"),
    ("call_qc", "query_qc_alerts"),
})

# MCP 级健康探活超时（initialize + list_tools）
_PROBE_TIMEOUT = 5.0


class RealMCPTransport:
    """USE_MOCK_MCP=false 时的 transport：MCP SDK client 调独立网关。"""

    def __init__(self, *, gateway_url: str | None = None, timeout: float = 30.0):
        url = (
            gateway_url
            if gateway_url is not None
            else getattr(settings, "MCP_GATEWAY_URL", "") or ""
        ).rstrip("/")
        self._gateway_base = url
        self._timeout = float(timeout)
        # 实例级长生命周期 HTTP 连接池：跨多次工具调用复用底层 TCP 连接。
        # 依据（2.1 spike，mcp 1.30.0 源码 streamable_http.py）：streamable_http_client
        # 对调用方提供的 http_client 不接管生命周期（client_provided 分支不 close），
        # MCP session 状态挂在每次新建的 StreamableHTTPTransport 上而非 client，
        # 故复用连接池不引发 stateless 会话串扰。
        # 已知限制（技术债，详见本 change design.md Open Questions）：mcp 1.30.0
        # _handle_sse_response 收到响应后提前 aclose SSE 流，httpx 不将该连接归还
        # keepalive 池 → TCP 层实际每次新建即关（无泄漏、连接数稳定）；本 client
        # 仍提供连接数上限保护与统一关停语义。
        # httpx 超时给 self._timeout + 2s 宽限：Agent 侧薄超时（asyncio.wait_for）
        # 是权威取消点，传输层仅兜底防止连接悬挂泄漏。
        self._http_client = httpx.AsyncClient(
            timeout=self._timeout + 2.0,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        self._closed = False

    async def aclose(self) -> None:
        """关闭共享 HTTP 连接池（幂等，供 lifespan shutdown 调用）。"""
        if self._closed:
            return
        self._closed = True
        await self._http_client.aclose()

    @property
    def endpoint(self) -> str:
        """网关 MCP streamable-http 端点（网关子应用内部路由 /mcp）。"""
        return f"{self._gateway_base}/mcp"

    async def call(
        self,
        server: str,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> Any:
        if server not in KNOWN_SERVERS:
            # 未知 server：客户端侧明确拒绝，不建连、不转发（spec：未知 server 返回明确错误）
            raise MCPError(
                server,
                tool,
                f"unknown MCP server: {server!r}, known={sorted(KNOWN_SERVERS)}",
            )
        if not self._gateway_base or is_placeholder_secret(self._gateway_base):
            raise MCPError(
                server,
                tool,
                "real MCP path requires MCP_GATEWAY_URL (standalone mcp-gateway); "
                "keep USE_MOCK_MCP=true for demo",
            )

        arguments = dict(params or {})
        meta = {"trace_id": trace_id, "session_id": session_id}
        logger.debug(
            "mcp_gateway_call",
            server=server, tool=tool, endpoint=self.endpoint,
            trace_id=trace_id, session_id=session_id,
        )
        try:
            result = await self._run_session(
                lambda session: session.call_tool(
                    f"{server}.{tool}", arguments, meta=meta
                ),
            )
        except MCPError:
            raise
        except Exception as e:  # noqa: BLE001 — 传输/协议异常统一分级映射
            raise self._map_error(server, tool, e) from e

        # 工具级错误：网关用 CallToolResult.isError + 文本返回（不抛 JSON-RPC 异常）
        if getattr(result, "isError", False):
            message = _error_text(result)
            if _is_permission_denied(message):
                raise MCPPermissionDeniedError(server, tool, message)
            raise MCPError(server, tool, f"gateway tool error: {message[:200]}")

        return _extract_payload(result, server, tool)

    async def probe(self) -> None:
        """MCP 级探活：initialize + list_tools（供 MCPGateway 健康检查）。

        成功即网关 MCP server 可连且协议握手通过；失败抛 app 级异常（MCPError 子类）。
        """
        if not self._gateway_base or is_placeholder_secret(self._gateway_base):
            raise MCPError(
                "", "list_tools", "real MCP path requires MCP_GATEWAY_URL"
            )
        try:
            await self._run_probe_session(lambda session: session.list_tools())
        except MCPError:
            raise
        except Exception as e:  # noqa: BLE001
            raise self._map_error("", "list_tools", e) from e

    async def _run_session(self, op: Any) -> Any:
        """复用实例级连接池执行一次 MCP 调用（MCP 会话层仍 stateless）。

        每次调用仍新建轻量 ClientSession 并 initialize（网关 stateless_http 不变）；
        仅底层 httpx.AsyncClient/TCP 连接池跨调用复用。timeout+2s 宽限语义
        固化在实例级 client 上（见 __init__）。
        """
        async with streamable_http_client(
            self.endpoint, http_client=self._http_client
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await op(session)

    async def _run_probe_session(self, op: Any) -> Any:
        """健康探活维持短生命周期临时连接（不进共享池，超时 5s 级）。"""
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT + 2.0) as http_client:
            async with streamable_http_client(
                self.endpoint, http_client=http_client
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await op(session)

    @staticmethod
    def _map_error(server: str, tool: str, exc: BaseException) -> MCPError:
        """SDK 传输/协议异常 → app 级异常。

        mcp SDK（anyio TaskGroup）常把根因包在 ExceptionGroup 中，需递归解包。
        """
        root = _deep_leaf(exc)
        if isinstance(root, httpx.TimeoutException):
            return MCPTimeoutError(server, tool, f"gateway timeout: {root}")
        if isinstance(root, httpx.HTTPStatusError):
            status = root.response.status_code
            if status in (401, 403):
                return MCPPermissionDeniedError(
                    server, tool, f"gateway auth failed: HTTP {status}"
                )
            return MCPError(server, tool, f"gateway http error: HTTP {status}")
        if isinstance(root, httpx.HTTPError):
            return MCPError(server, tool, f"gateway http error: {root}")
        return MCPError(
            server, tool, f"gateway mcp call failed: {type(exc).__name__}: {exc}"
        )


def _deep_leaf(exc: BaseException) -> BaseException:
    """递归取 ExceptionGroup 内首个叶子异常（优先 httpx 类）；无组则返回自身。"""
    leaves: list[BaseException] = []

    def walk(e: BaseException) -> None:
        group = getattr(e, "exceptions", None)
        if group:
            for sub in group:
                walk(sub)
        else:
            leaves.append(e)

    walk(exc)
    for leaf in leaves:
        if isinstance(leaf, httpx.HTTPError):
            return leaf
    return leaves[0] if leaves else exc


def _is_permission_denied(message: str) -> bool:
    """识别网关/协议层权限拒绝（B0002 / 401 / 403 / unauthorized）。"""
    text = (message or "").upper()
    return "B0002" in text or "401" in text or "403" in text or "UNAUTHORIZED" in text


def _error_text(result: Any) -> str:
    """提取 isError CallToolResult 的文本内容。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return " | ".join(parts)[:300] if parts else "unknown gateway tool error"


def _extract_payload(result: Any, server: str = "", tool: str = "") -> Any:
    """CallToolResult → 工具原始返回值（dict/list，形状对齐现有 fixture）。

    mcp 1.30 FastMCP 实测：
    - dict 返回 → 1 个 TextContent block，内容为完整 dict 的 JSON
    - list 返回 → 每元素 1 个 TextContent block（空列表 0 block）
    - structuredContent 均为 None
    故按 (server, tool) 静态基数还原（见 _LIST_SHAPE_TOOLS）：
    list 型工具聚合全部 block；dict 型工具取首个 block。
    保留 structuredContent 分支兼容其它 SDK 版本：FastMCP 对非 dict 返回值
    包装为 ``{"result": [...]}``，dict 返回原样透传。
    """
    want_list = (server, tool) in _LIST_SHAPE_TOOLS

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if set(structured.keys()) == {"result"}:
            payload = structured["result"]
            return payload if isinstance(payload, list) or not want_list else [payload]
        return structured

    items: list[Any] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            items.append(json.loads(text))
        except ValueError:
            items.append(text)

    if want_list:
        return items
    return items[0] if items else None
