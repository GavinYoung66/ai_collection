"""真路径 MCP 配置校验（DEBT-04 / design D6）。"""
from __future__ import annotations

from app.config import settings
from common.llm.reliability import is_placeholder_secret


class MCPConfigError(RuntimeError):
    """USE_MOCK_MCP=false 时缺少桥接/网关配置。"""


def validate_real_mcp_config(*, use_mock: bool | None = None) -> None:
    """Mock 关闭时要求 ``RAG_SERVICE_URL``（D2 进程内 bridge）非占位。

    ``MCP_GATEWAY_URL`` 仅在调用非 ``rag_kb`` 工具时需要；启动阶段强制 bridge URL，
    保证判责链路的 regulations/templates 真路径可配置。
    """
    mock = settings.USE_MOCK_MCP if use_mock is None else use_mock
    if mock:
        return

    rag_url = (getattr(settings, "RAG_SERVICE_URL", "") or "").strip()
    if is_placeholder_secret(rag_url):
        raise MCPConfigError(
            "USE_MOCK_MCP=false requires RAG_SERVICE_URL "
            "(e.g. http://rag-service:8000); keep USE_MOCK_MCP=true for Demo"
        )
