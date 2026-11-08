"""Mock MCP Client（T121 薄转发层）。

模块本体已迁入 app/mock/mcp/mock_client.py；旧导入路径
``from tests.mock.mcp.mock_client import ...`` 保持零改动兼容。
"""
from app.mock.mcp.mock_client import ALL_TOOLS, SERVERS, MockMCPClient  # noqa: F401
