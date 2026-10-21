"""MCP 网关连接管理。

支持两种模式（由 ComplaintSettings.USE_MOCK_MCP 决定，design D6）：
- USE_MOCK_MCP=true（dev，默认）：进程内 MockMCPClient，SHALL NOT 连接网关
- USE_MOCK_MCP=false（prod）：RealMCPTransport 经 MCP SDK client 连独立容器
  mcp-gateway（地址由 MCP_GATEWAY_URL 注入），SHALL NOT 进程内嵌桥

提供 get_client(server) -> MCPClient 工厂方法，与 MCPClient 联动。
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
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
        # Batch 5：真路径经 MCP SDK client 连独立网关，不再传任何进程内桥
        from app.mcp.real_transport import RealMCPTransport

        return RealMCPTransport(gateway_url=self._gateway_url)

    async def ahealth_check(self) -> dict[str, str]:
        """异步健康检查钩子（MCP 协议级探活）。

        Mock：全部 healthy，不发起网络请求。
        真路径：对网关 MCP server 做 initialize + list_tools 短探活（5s）。
        网关是 4 server 的统一入口，探活结果对 4 server 一致：
        握手成功 → healthy；权限/协议错 → degraded；连不上/超时 → unavailable。
        """
        if getattr(settings, "USE_MOCK_MCP", True):
            for server in ALLOWED_SERVERS:
                self._health[server] = "healthy"
            return dict(self._health)

        from app.mcp.errors import MCPError, MCPPermissionDeniedError
        from app.mcp.real_transport import RealMCPTransport

        status = "healthy"
        try:
            await RealMCPTransport(gateway_url=self._gateway_url).probe()
        except MCPPermissionDeniedError:
            # 网关可达但鉴权拦截：凭证问题，标记 degraded 而非不可达
            status = "degraded"
        except MCPError as e:
            logger.warning("mcp_gateway_probe_failed", error=str(e))
            status = "unavailable"
        for server in ALLOWED_SERVERS:
            self._health[server] = status
        return dict(self._health)

    def health_check(self) -> dict[str, str]:
        """同步健康检查钩子：返回各 server 状态（healthy / degraded / unavailable）。

        Mock：全部 healthy，不发起网络请求。
        真路径：HTTP GET 网关 ``/health`` 短探活（3s）。``/health`` 与 MCP 端点
        同属网关一个 FastAPI 进程，进程存活即 MCP server 在；需要协议级深探活
        （initialize/list_tools）用异步 ``ahealth_check``。
        """
        if getattr(settings, "USE_MOCK_MCP", True):
            for server in ALLOWED_SERVERS:
                self._health[server] = "healthy"
            return dict(self._health)

        base = (self._gateway_url or "").strip().rstrip("/")
        if not base:
            for server in ALLOWED_SERVERS:
                self._health[server] = "unavailable"
            return dict(self._health)

        try:
            resp = httpx.get(f"{base}/health", timeout=3.0)
            if resp.status_code == 200:
                status = "healthy"
            elif resp.status_code in (401, 403):
                status = "degraded"
            else:
                status = "unavailable"
        except httpx.HTTPError as e:
            logger.warning("mcp_gateway_health_probe_failed", error=str(e))
            status = "unavailable"
        for server in ALLOWED_SERVERS:
            self._health[server] = status
        return dict(self._health)
