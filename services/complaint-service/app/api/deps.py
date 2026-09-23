"""complaint-service 依赖注入工厂。

- get_session：复用 common.db.session（不重写）
- get_mcp_client：返回 MCPClient 单例（dev 默认 Mock，prod 真实）
- get_llm_provider：返回 ComplaintLLMProvider 单例（dev 默认 Mock，prod 真实）
"""
from __future__ import annotations

from typing import Any, Optional

# DB session：直接复用 common，不重复实现
from common.db.session import get_session  # noqa: F401  (re-exported for Depends)

# ── MCP Client 单例 ──
_mcp_client: Optional[Any] = None


def _build_mcp_client() -> Any:
    """构建 MCP Client：始终返回 MCPClient（不再返回 MCPGateway）。

    - USE_MOCK_MCP=True：transport=MockMCPClient（覆盖全部 PRD 4.4 server/tool）
    - USE_MOCK_MCP=False：RealMCPTransport（rag_kb → HTTP bridge；business_ticket/call_qc/data_platform → 批次③进程内桥）
    """
    from app.mcp.gateway import MCPGateway

    return MCPGateway().get_shared_client()


def get_mcp_client() -> Any:
    """获取 MCP Client 单例（不再返回 None）。"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = _build_mcp_client()
    return _mcp_client


def set_mcp_client(client: Any) -> None:
    """供 main.py startup 注入真实 MCP Client（覆盖默认）。"""
    global _mcp_client
    _mcp_client = client


def reset_mcp_client() -> None:
    """测试用：清空 MCP 单例。"""
    global _mcp_client
    _mcp_client = None


# ── LLM Provider 单例 ──
_llm_provider: Optional[Any] = None


def _build_llm_provider() -> Any:
    """构建 LLM Provider：dev 用 Mock，prod 用真实 common.llm。"""
    from app.llm.provider import ComplaintLLMProvider

    return ComplaintLLMProvider()  # 内部根据 USE_MOCK_LLM 选择 transport


def get_llm_provider() -> Any:
    """获取 LLM Provider 单例（不再返回 None）。"""
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = _build_llm_provider()
    return _llm_provider


def set_llm_provider(provider: Any) -> None:
    """供 main.py startup 注入真实 LLM Provider（覆盖默认）。"""
    global _llm_provider
    _llm_provider = provider


def reset_llm_provider() -> None:
    """测试用：清空单例，下次 get_llm_provider 按当前 settings 重建。"""
    global _llm_provider
    _llm_provider = None
