"""真路径 MCP 配置校验（DEBT-04 / design D6）。"""
from __future__ import annotations

from app.config import settings
from common.llm.reliability import is_placeholder_secret


class MCPConfigError(RuntimeError):
    """USE_MOCK_MCP=false 时缺少桥接/网关配置。"""


def validate_real_mcp_config(*, use_mock: bool | None = None) -> None:
    """Mock 关闭时要求 ``MCP_GATEWAY_URL``（design D2/D6 独立网关）非占位。

    真路径经 MCP SDK client 连独立 mcp-gateway，下游路由由网关承接；
    进程内桥（RAG_SERVICE_URL 等）已随桥迁移废弃，不再是真路径依赖。
    """
    mock = settings.USE_MOCK_MCP if use_mock is None else use_mock
    if mock:
        return

    gateway_url = (getattr(settings, "MCP_GATEWAY_URL", "") or "").strip()
    if is_placeholder_secret(gateway_url):
        raise MCPConfigError(
            "USE_MOCK_MCP=false requires MCP_GATEWAY_URL "
            "(e.g. http://mcp-gateway:9000); keep USE_MOCK_MCP=true for Demo"
        )
