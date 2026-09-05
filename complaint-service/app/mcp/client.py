from pydantic import BaseModel
from typing import Any


class MCPTimeoutError(Exception):
    pass


class MCPError(Exception):
    pass


class MCPClient:
    async def call(self, server: str, tool: str, params: dict) -> Any:
        raise NotImplementedError("MCPClient.call() 需要对接实际MCP网关实现")

    async def health_check(self, server: str) -> bool:
        raise NotImplementedError


mcp_client = MCPClient()
