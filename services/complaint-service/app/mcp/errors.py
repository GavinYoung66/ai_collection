"""MCP 异常类型。

批次3 将 MCPError / MCPTimeoutError 从 tests/mock/mcp/errors.py 迁到此处，
作为 complaint-service MCP 层的权威异常定义。批次0 Mock 回补后可复用本模块。
"""
from __future__ import annotations


class MCPError(Exception):
    """MCP 调用错误（业务类 / 服务故障 / 权限拒绝均通过子类或 message 区分）。"""

    def __init__(self, server: str, tool: str, message: str = ""):
        self.server = server
        self.tool = tool
        super().__init__(f"MCP error [{server}.{tool}]: {message}")


class MCPTimeoutError(MCPError):
    """MCP 调用超时（服务故障类，不重试）。"""

    def __init__(self, server: str, tool: str, message: str = "timeout"):
        super().__init__(server, tool, message)


class MCPPermissionDeniedError(MCPError):
    """MCP 权限拒绝（终止并安全审计）。"""

    def __init__(self, server: str, tool: str, message: str = "permission denied"):
        super().__init__(server, tool, message)
