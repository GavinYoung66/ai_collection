"""真路径 MCP transport（gateway standalone 等价物，design D2 / D5）。

- ``rag_kb`` 支持工具：进程内 ``RagKbBridge`` → rag-service HTTP
- ``business_ticket`` / ``call_qc`` / ``data_platform``：批次③ 进程内桥
  （快照 / qc-source / 质检台账 / 内部回写口），不要求 MCP_GATEWAY_URL
- 其它未知 server：若配置了 ``MCP_GATEWAY_URL``，转发 HTTP；否则明确依赖错误
- 禁止对已支持工具抛 ``NotImplementedError``
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import settings
from app.mcp.business_ticket_bridge import BusinessTicketBridge
from app.mcp.call_qc_bridge import CallQcBridge
from app.mcp.data_platform_bridge import DataPlatformBridge
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
        data_platform_url: str | None = None,
        qc_service_url: str | None = None,
        business_bridge: BusinessTicketBridge | None = None,
        call_qc_bridge: CallQcBridge | None = None,
        data_platform_bridge: DataPlatformBridge | None = None,
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
        dp_url = (
            data_platform_url
            if data_platform_url is not None
            else getattr(settings, "DATA_PLATFORM_URL", "") or ""
        ).rstrip("/")
        qc_url = (
            qc_service_url
            if qc_service_url is not None
            else getattr(settings, "QC_SERVICE_URL", "") or ""
        ).rstrip("/")

        self._bridge = bridge or RagKbBridge(rag_url)
        self._business_bridge = business_bridge or BusinessTicketBridge(dp_url)
        self._call_qc_bridge = call_qc_bridge or CallQcBridge(dp_url, qc_url)
        self._data_platform_bridge = data_platform_bridge or DataPlatformBridge(dp_url)

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

        if server == "business_ticket":
            return await self._business_bridge.call_tool(
                tool, params, trace_id=trace_id, session_id=session_id
            )
        if server == "call_qc":
            return await self._call_qc_bridge.call_tool(
                tool, params, trace_id=trace_id, session_id=session_id
            )
        if server == "data_platform":
            return await self._data_platform_bridge.call_tool(
                tool, params, trace_id=trace_id, session_id=session_id
            )

        # 未知 server 保留 gateway 转发防御分支
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
