"""真路径 MCP transport（gateway standalone 等价物，design D2）。

- ``rag_kb`` 支持工具：进程内 ``RagKbBridge`` → rag-service HTTP
- 其它 server：若配置了 ``MCP_GATEWAY_URL``，转发 HTTP；否则明确依赖错误
- 禁止对已支持工具抛 ``NotImplementedError``
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import settings
from app.mcp.errors import MCPError, MCPTimeoutError
from app.mcp.rag_kb_bridge import RagKbBridge
from common.llm.reliability import is_placeholder_secret

logger = structlog.get_logger()

_RAG_SUPPORTED = frozenset({"search_regulations", "search_response_templates"})
_RAG_RESERVED = frozenset({"search_complaint_handling_guidelines"})


class RealMCPTransport:
    """USE_MOCK_MCP=false 时的统一 transport。"""

    def __init__(
        self,
        *,
        gateway_url: str | None = None,
        rag_base_url: str | None = None,
        bridge: RagKbBridge | None = None,
    ):
        self._gateway_url = (
            gateway_url
            if gateway_url is not None
            else getattr(settings, "MCP_GATEWAY_URL", "") or ""
        ).rstrip("/")
        rag_url = (
            rag_base_url
            if rag_base_url is not None
            else getattr(settings, "RAG_SERVICE_URL", "") or ""
        )
        self._bridge = bridge or RagKbBridge(rag_url)

    async def call(
        self,
        server: str,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> Any:
        if server == "rag_kb":
            if tool in _RAG_SUPPORTED or tool in _RAG_RESERVED:
                return await self._bridge.call_tool(
                    tool, params, trace_id=trace_id, session_id=session_id
                )
            raise MCPError("rag_kb", tool, f"unsupported rag_kb tool: {tool}")

        return await self._forward_gateway(
            server, tool, params, trace_id=trace_id, session_id=session_id
        )

    async def _forward_gateway(
        self,
        server: str,
        tool: str,
        params: dict,
        *,
        trace_id: str,
        session_id: str,
    ) -> Any:
        if not self._gateway_url or is_placeholder_secret(self._gateway_url):
            raise MCPError(
                server,
                tool,
                "real MCP path: MCP_GATEWAY_URL missing for non-rag_kb tools "
                "(or keep USE_MOCK_MCP=true)",
            )
        url = f"{self._gateway_url}/mcp/call"
        payload = {
            "server": server,
            "tool": tool,
            "params": params or {},
            "trace_id": trace_id,
            "session_id": session_id,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as e:
            raise MCPTimeoutError(server, tool, f"gateway timeout: {e}") from e
        except httpx.HTTPError as e:
            raise MCPError(server, tool, f"gateway http error: {e}") from e

        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data
