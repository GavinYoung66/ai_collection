"""Mock MCP 异常类型（回补批次3：复用 app.mcp.errors 权威定义）。

批次3 已将 MCPError / MCPTimeoutError 迁到 app/mcp/errors.py。
本模块从 app.mcp.errors re-export，保持向后兼容（已有 from tests.mock.mcp.errors import ... 的代码不受影响）。
"""
from app.mcp.errors import MCPError, MCPTimeoutError  # noqa: F401

__all__ = ["MCPError", "MCPTimeoutError"]
