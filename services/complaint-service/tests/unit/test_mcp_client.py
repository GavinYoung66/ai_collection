"""MCP Client 单元测试。

覆盖：
- 成功调用 + 审计（G6：可靠性委托 common.mcp.reliability.call_tool）
- 超时不重试（MCPTimeoutError）；业务错 reliability 内部重试 3 次
- 6.2：rag_kb 连续超时 5 次 → 熔断 open（key=csmp:mcp_degraded:rag_kb）
- 6.2：rag_kb 熔断期 business_ticket 不受影响（按 server 隔离）
- 6.2：熔断期 compliance 走规则兜底（[规则兜底] + pending_review）
"""
from __future__ import annotations

import pytest

from app.mcp.client import MCPClient
from app.mcp.errors import MCPError, MCPTimeoutError


class _FakeRedis:
    """degraded flag 用内存 Redis（setex/get），避免真实连接超时拖慢测试。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def setex(self, key, ttl, value):  # noqa: ARG002
        self.store[key] = str(value)

    async def set(self, key, value, ex=None, nx=False):  # noqa: ARG002
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def ping(self):
        return True

    async def incr(self, key):  # 维3 令牌桶默认计数恒 1（窗口内首次，放行）
        return 1

    async def expire(self, key, window):  # noqa: ARG002
        return None


@pytest.fixture(autouse=True)
def _clean_reliability():
    """熔断注册表 / 全局 transport / Redis 隔离（reliability 层为进程内全局态）。"""
    from common.mcp import reliability as mcp_rel
    from common.resilience._redis import configure_redis, reset_redis

    mcp_rel._mcp_breakers.clear()
    mcp_rel.reset_mcp_transport()
    configure_redis(_FakeRedis())
    yield
    mcp_rel._mcp_breakers.clear()
    mcp_rel.reset_mcp_transport()
    reset_redis()


@pytest.fixture
def client():
    return MCPClient()


@pytest.mark.asyncio
async def test_mock_call_success(client):
    data = await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1", session_id="s1")
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_timeout_raises_mcp_timeout_error_no_retry(client):
    client._get_transport().set_mode("query_case_info", "timeout")
    with pytest.raises(MCPTimeoutError):
        await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1")
    # 超时不重试：transport 只被触达 1 次
    assert len(client._get_transport().call_log) == 1


@pytest.mark.asyncio
async def test_business_error_retries_3_inside_reliability(client):
    client._get_transport().set_mode("query_case_info", "error")
    with pytest.raises(MCPError):
        await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1")
    # 业务错重试 3 次（reliability 层内部），audit 记录最终结果 1 条
    assert len(client._get_transport().call_log) == 3
    error_entries = [e for e in client.call_log if e["status"] == "error"]
    assert len(error_entries) == 1
    assert error_entries[0]["error"].startswith("business_error")


@pytest.mark.asyncio
async def test_audit_log_recorded(client):
    await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]}, trace_id="t1", session_id="s1")
    assert len(client.call_log) == 1
    entry = client.call_log[0]
    assert entry["server"] == "business_ticket"
    assert entry["tool"] == "query_case_info"
    assert entry["status"] == "success"
    assert entry["trace_id"] == "t1"


# ── 6.2：per-server 熔断（key=csmp:mcp_degraded:{server}）──


async def _trip_rag_kb_circuit(client: MCPClient) -> None:
    for _ in range(5):
        client._get_transport().set_mode("search_regulations", "timeout")
        with pytest.raises(MCPTimeoutError):
            await client.call("rag_kb", "search_regulations", {"query": "夜间催收"}, trace_id="t1")
        client._get_transport().reset_mode("search_regulations")


@pytest.mark.asyncio
async def test_rag_kb_5_timeouts_trip_circuit(client):
    from common.mcp.reliability import MCPCircuitOpenError

    await _trip_rag_kb_circuit(client)
    touched = len(client._get_transport().call_log)

    # 第 6 次：熔断 open，快速拒绝且不再触达 transport
    with pytest.raises(MCPCircuitOpenError, match="csmp:mcp_degraded:rag_kb"):
        await client.call("rag_kb", "search_regulations", {"query": "q"}, trace_id="t1")
    assert len(client._get_transport().call_log) == touched
    # 审计记录 circuit_open
    assert client.call_log[-1]["error"] == "circuit_open"


@pytest.mark.asyncio
async def test_business_ticket_not_affected_by_rag_breaker(client):
    from common.mcp.reliability import MCPCircuitOpenError

    await _trip_rag_kb_circuit(client)
    with pytest.raises(MCPCircuitOpenError):
        await client.call("rag_kb", "search_regulations", {"query": "q"})

    # business_ticket 独立熔断 key（csmp:mcp_degraded:business_ticket），正常调用
    data = await client.call("business_ticket", "query_case_info", {"case_ids": ["c1"]})
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_compliance_rule_fallback_on_rag_circuit_open(client):
    """rag_kb 熔断 open 期：compliance 节点整体走规则兜底（[规则兜底] + pending_review）。"""
    from app.api.deps import reset_mcp_client, set_mcp_client
    from app.graph.nodes.compliance import compliance_node
    from app.models.state import ComplaintSessionState

    await _trip_rag_kb_circuit(client)
    set_mcp_client(client)
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


# ── 维3：per-server 令牌桶接线（spec rate-limit 维度3）──


@pytest.mark.asyncio
async def test_mcp_token_bucket_rejects_without_touching_transport(client):
    """rag_kb 令牌桶耗尽 → MCPError 拒绝、不触达 server；
    csmp.rate_limit.hit 事件携带 node/trace_id；审计记 rate_limited。"""
    from common.resilience._redis import configure_redis
    from common.resilience import hooks
    from app.events.publisher import publisher
    from app.main import wire_resilience_hooks

    class _AlwaysLimitedRedis(_FakeRedis):
        async def incr(self, key):  # 窗口计数恒超 qps
            return 999

    configure_redis(_AlwaysLimitedRedis())
    publisher.clear()
    wire_resilience_hooks()
    try:
        with pytest.raises(MCPError, match="令牌桶"):
            await client.call(
                "rag_kb", "search_regulations", {"query": "夜间催收"},
                trace_id="trace-mcp-limit",
            )
        assert len(client._get_transport().call_log) == 0
        assert client.call_log[-1]["error"] == "rate_limited"
        hits = publisher.list_events("csmp.rate_limit.hit")
        assert hits, "令牌桶耗尽未发 csmp.rate_limit.hit"
        payload = hits[0].payload
        assert payload["node"] == "mcp:rag_kb"
        assert payload["trace_id"] == "trace-mcp-limit"
    finally:
        hooks.set_publisher(None)
        publisher.clear()


@pytest.mark.asyncio
async def test_breaker_open_event_carries_trace_context(client):
    """熔断 open 事件透传业务上下文（trace_id/node），payload 保留 key/reason。"""
    from common.resilience import hooks
    from app.events.publisher import publisher
    from app.main import wire_resilience_hooks

    publisher.clear()
    wire_resilience_hooks()
    try:
        await _trip_rag_kb_circuit(client)
        opens = publisher.list_events("csmp.circuit_breaker.open")
        assert opens, "rag_kb 熔断翻转未发 csmp.circuit_breaker.open"
        payload = opens[0].payload
        assert payload["key"] == "csmp:mcp_degraded:rag_kb"
        assert payload["reason"] == "circuit_open"
        assert payload["node"] == "mcp:rag_kb"
        assert payload["trace_id"] == "t1"  # _trip_rag_kb_circuit 固定 trace
    finally:
        hooks.set_publisher(None)
        publisher.clear()
