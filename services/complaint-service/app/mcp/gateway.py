"""MCP 网关连接管理。

支持两种模式（由 ComplaintSettings.MCP_GATEWAY_MODE 决定）：
- inline + USE_MOCK_MCP=true（dev）：进程内 MockMCPClient
- USE_MOCK_MCP=false：RealMCPTransport（D2 进程内 rag_kb bridge；standalone 等价）

提供 get_client(server) -> MCPClient 工厂方法，与 MCPClient 联动。
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from app.config import settings
from app.mcp.client import MCPClient

logger = structlog.get_logger()

# PRD §4.4 权威 4 Server 名
ALLOWED_SERVERS = {"business_ticket", "call_qc", "rag_kb", "data_platform"}


class MCPGateway:
    """MCP 网关：连接管理 + 健康检查 + Client 工厂。"""

    def __init__(self, mode: Optional[str] = None, gateway_url: Optional[str] = None):
        self._mode = mode or settings.MCP_GATEWAY_MODE
        self._gateway_url = gateway_url or getattr(settings, "MCP_GATEWAY_URL", "")
        self._clients: dict[str, MCPClient] = {}
        self._health: dict[str, str] = {}  # server -> healthy | degraded | unavailable

    @property
    def mode(self) -> str:
        return self._mode

    def get_client(self, server: str) -> MCPClient:
        """获取指定 server 的 MCPClient（带连接池复用）。"""
        if server not in ALLOWED_SERVERS:
            raise ValueError(f"unknown MCP server: {server} (allowed: {sorted(ALLOWED_SERVERS)})")
        if server not in self._clients:
            transport = self._build_transport(server)
            self._clients[server] = MCPClient(transport=transport)
        return self._clients[server]

    def get_shared_client(self) -> MCPClient:
        """返回可路由全部 server 的 MCPClient（真/Mock 均按 call(server,…) 分发）。"""
        return MCPClient(transport=self._build_transport("_shared"))

    def _build_transport(self, server: str) -> Any:
        """根据 Mock 开关构建底层 transport。"""
        if getattr(settings, "USE_MOCK_MCP", True):
            from tests.mock.mcp.mock_client import MockMCPClient

            return MockMCPClient()
        from app.mcp.real_transport import RealMCPTransport

        return RealMCPTransport(gateway_url=self._gateway_url)

    def health_check(self) -> dict[str, str]:
        """健康检查钩子：返回各 server 状态。

        Mock：全部 healthy。
        真路径：有 RAG_SERVICE_URL 则 rag_kb 视为配置就绪；网关 URL 可选探活。
        """
        if getattr(settings, "USE_MOCK_MCP", True):
            for server in ALLOWED_SERVERS:
                self._health[server] = "healthy"
            return dict(self._health)

        rag_url = (getattr(settings, "RAG_SERVICE_URL", "") or "").strip()
        dp_url = (getattr(settings, "DATA_PLATFORM_URL", "") or "").strip()
        qc_url = (getattr(settings, "QC_SERVICE_URL", "") or "").strip()
        from common.llm.reliability import is_placeholder_secret

        rag_ok = not is_placeholder_secret(rag_url)
        # 批次③：三 server 走进程内桥，就绪看各自下游 URL，与 MCP_GATEWAY_URL 无关
        self._health["rag_kb"] = "healthy" if rag_ok else "unavailable"
        self._health["business_ticket"] = "healthy" if dp_url else "unavailable"
        self._health["data_platform"] = "healthy" if dp_url else "unavailable"
        self._health["call_qc"] = "healthy" if dp_url and qc_url else "unavailable"
        return dict(self._health)
