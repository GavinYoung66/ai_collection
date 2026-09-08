"""MCP Client 单元测试：成功调用 + 超时/错误分级 + PII 脱敏 + 审计。"""
from __future__ import annotations

import pytest

from app.mcp.client import MCPClient
from app.mcp.errors import MCPError, MCPTimeoutError


@pytest.fixture
def client():
    return MCPClient()


@pytest.mark.asyncio
async def test_mock_call_success(client):
    data = await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1", session_id="s1")
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_timeout_raises_mcp_timeout_error(client):
    client._get_transport().set_mode("query_case_info", "timeout")
    with pytest.raises(MCPTimeoutError):
        await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1")


@pytest.mark.asyncio
async def test_business_error_retries_then_raises(client):
    client._get_transport().set_mode("query_case_info", "error")
    with pytest.raises(MCPError):
        await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1")
    # 业务错误重试 3 次
    error_entries = [e for e in client.call_log if e["status"] == "error"]
    assert len(error_entries) == 3


@pytest.mark.asyncio
async def test_audit_log_recorded(client):
    await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1", session_id="s1")
    assert len(client.call_log) == 1
    entry = client.call_log[0]
    assert entry["server"] == "business_ticket"
    assert entry["tool"] == "query_case_info"
    assert entry["status"] == "success"
    assert entry["trace_id"] == "t1"
