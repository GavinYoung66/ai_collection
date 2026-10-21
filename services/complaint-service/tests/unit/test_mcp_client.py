"""MCP Client 单元测试（Batch 5：Agent 侧薄封装）。

架构边界（mcp-gateway-standalone Batch 5）：
- Agent 侧仅保留薄超时（asyncio.wait_for，不重试）、PII 二次校验、调用审计
- 可靠性编排（per-server 熔断 / 业务错重试 3 次 / per-server 限流）已迁
  独立 mcp-gateway，对应网关侧覆盖见 services/mcp-gateway/tests/test_reliability.py
- USE_MOCK_MCP=true 时注入 MockMCPClient，不连网关
"""
from __future__ import annotations

import asyncio

import pytest

from app.mcp.client import MCPClient
from app.mcp.errors import MCPError, MCPPermissionDeniedError, MCPTimeoutError


@pytest.fixture
def client():
    return MCPClient()


@pytest.mark.asyncio
async def test_mock_call_success(client):
    data = await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1", session_id="s1")
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_timeout_raises_mcp_timeout_error_no_retry(client):
    """transport 已分类超时：直接抛 MCPTimeoutError，Agent 侧不重试。"""
    client._get_transport().set_mode("query_case_info", "timeout")
    with pytest.raises(MCPTimeoutError):
        await client.call("business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="t1")
    # Agent 侧不重试：transport 只被触达 1 次
    assert len(client._get_transport().call_log) == 1
    assert client.call_log[-1]["error"] == "timeout"


@pytest.mark.asyncio
async def test_business_error_no_agent_side_retry(client):
    """业务错误在网关侧重试（单层）；Agent 侧 SHALL NOT 重试，只触达 1 次。"""
    client._get_transport().set_mode("query_case_info", "error")
    with pytest.raises(MCPError):
        await client.call("business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="t1")
    assert len(client._get_transport().call_log) == 1
    error_entries = [e for e in client.call_log if e["status"] == "error"]
    assert len(error_entries) == 1
    assert error_entries[0]["error"].startswith("business_error")


@pytest.mark.asyncio
async def test_audit_log_recorded(client):
    await client.call("business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="t1", session_id="s1")
    assert len(client.call_log) == 1
    entry = client.call_log[0]
    assert entry["server"] == "business_ticket"
    assert entry["tool"] == "query_case_info"
    assert entry["status"] == "success"
    assert entry["trace_id"] == "t1"
    assert entry["session_id"] == "s1"


# ── Batch 5：薄超时（Agent 侧快速失败，不重试）──


class _HangingTransport:
    async def call(self, server, tool, params, **kwargs):
        await asyncio.sleep(100)


@pytest.mark.asyncio
async def test_thin_timeout_cancels_hanging_gateway():
    """网关响应超过 Agent 薄超时：wait_for 取消并抛 MCPTimeoutError，审计记 timeout。"""
    client = MCPClient(transport=_HangingTransport(), timeout=0.1)
    with pytest.raises(MCPTimeoutError, match="timeout after 0.1s"):
        await client.call("rag_kb", "search_regulations", {"query": "q"}, trace_id="t1")
    assert client.call_log[-1]["error"] == "timeout"
    assert client.call_log[-1]["status"] == "error"


class _PermissionDeniedTransport:
    async def call(self, server, tool, params, **kwargs):
        raise MCPPermissionDeniedError(server, tool, "B0002 unauthorized")


@pytest.mark.asyncio
async def test_permission_denied_terminates_and_audits():
    """网关权限拒绝：终止、不重试、写 permission_denied 审计。"""
    transport = _PermissionDeniedTransport()
    client = MCPClient(transport=transport)
    with pytest.raises(MCPPermissionDeniedError):
        await client.call("rag_kb", "search_regulations", {"query": "q"}, trace_id="t1")
    assert client.call_log[-1]["error"] == "permission_denied"
    assert len(client.call_log) == 1  # 不重试 → 仅 1 条审计


# ── Batch 5：PII 二次校验（defense-in-depth）──


class _PiiLeakTransport:
    """模拟网关漏脱敏：返回含明文手机号/身份证。"""

    async def call(self, server, tool, params, **kwargs):
        return {
            "phone": "13800138000",
            "id_card": "110101199001011234",
            "nested": [{"mobile": "13912345678"}],
        }


@pytest.mark.asyncio
async def test_pii_second_line_sanitizes_recursively():
    client = MCPClient(transport=_PiiLeakTransport())
    result = await client.call("business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="t1")
    blob = str(result)
    assert "13800138000" not in blob
    assert "110101199001011234" not in blob
    assert "13912345678" not in blob
    assert client.call_log[-1]["status"] == "success"  # 脱敏后成功返回，不当失败


# ── Graph 节点对 circuit_open 的既有防御分支（节点代码未动，分支覆盖保留）──


class _CircuitOpenStub:
    """直接注入到 deps 的桩：mcp.call 抛 MCPCircuitOpenError。

    新架构下熔断在网关，MCPClient 不再抛此异常；compliance 节点保留对该类型的
    防御分支，此用例仅锁定节点行为，不断言 Agent 侧可靠性编排。
    """

    async def call(self, server, tool, params, **kwargs):
        from common.mcp.reliability import MCPCircuitOpenError

        raise MCPCircuitOpenError("csmp:mcp_degraded:rag_kb")


@pytest.mark.asyncio
async def test_compliance_rule_fallback_on_rag_circuit_open():
    """rag_kb 熔断 open：compliance 节点整体走规则兜底（[规则兜底] + pending_review）。"""
    from app.api.deps import reset_mcp_client, set_mcp_client
    from app.graph.nodes.compliance import compliance_node
    from app.models.state import ComplaintSessionState

    set_mcp_client(_CircuitOpenStub())
    try:
        state = ComplaintSessionState(
            session_id="s-circuit",
            trace_id="trace-circuit",
            complaint_id="c-circuit",
            complaint_text="暴力催收投诉",
        )
        update = await compliance_node(state)
    finally:
        reset_mcp_client()

    assert update["task_status"] == "pending_review"
    assert update["exception_info"]["error_type"] == "circuit_open"
    assert update["exception_info"]["node"] == "compliance"
    opinion = update["compliance_opinion"]
    assert opinion.overall_compliance == "uncertain"
    assert "[规则兜底]" in opinion.details
