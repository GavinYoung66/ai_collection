"""common.mcp.reliability 单测：错误分级 + per-server 熔断 + 超时层级。"""
import asyncio

import pytest

from common.llm import reliability as llm_reliability_mod
from common.llm.reliability import invoke_llm, set_default_service, set_llm_transport
from common.mcp import reliability as mcp_mod
from common.mcp.reliability import (
    MCPCircuitOpenError,
    call_tool,
    reset_mcp_transport,
    set_mcp_transport,
)
from common.resilience._redis import configure_redis, reset_redis
from common.resilience.timeout import Timeout


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):
        return None

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _isolate():
    fake = FakeRedis()
    configure_redis(fake)
    set_default_service("csmp")
    mcp_mod._mcp_breakers.clear()
    llm_reliability_mod._llm_breakers.clear()
    reset_mcp_transport()
    set_llm_transport(None)
    yield fake
    set_default_service(None)
    reset_redis()
    reset_mcp_transport()
    set_llm_transport(None)


@pytest.mark.asyncio
async def test_timeout_not_retried_and_marks_degraded(_isolate):
    calls = {"n": 0}

    async def transport(server, tool, payload, timeout_seconds):
        calls["n"] += 1
        await asyncio.sleep(0.05)  # 超过 total=0.01
        return {}

    set_mcp_transport(transport)

    with pytest.raises(asyncio.TimeoutError):
        await call_tool("rag_kb", "search", {}, timeout=Timeout(total=0.01))

    assert calls["n"] == 1  # 超时不重试
    assert "csmp:mcp_degraded:rag_kb" in _isolate.store  # degraded 标志已置位


@pytest.mark.asyncio
async def test_permission_denied_no_retry_no_breaker_count(_isolate):
    calls = {"n": 0}

    async def transport(server, tool, payload, timeout_seconds):
        calls["n"] += 1
        raise PermissionError("unauthorized tool")

    set_mcp_transport(transport)

    with pytest.raises(PermissionError):
        await call_tool("rag_kb", "admin", {})

    assert calls["n"] == 1  # 不重试，直接终止
    breaker = mcp_mod._mcp_breakers.get("csmp:mcp_degraded:rag_kb")
    assert breaker is not None and breaker.failures == 0  # 不计熔断失败
    assert "csmp:mcp_degraded:rag_kb" not in _isolate.store  # 不标 degraded


@pytest.mark.asyncio
async def test_business_error_retries_three_times(monkeypatch, _isolate):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def transport(server, tool, payload, timeout_seconds):
        calls["n"] += 1
        raise RuntimeError("downstream 500")

    set_mcp_transport(transport)

    with pytest.raises(RuntimeError, match="downstream 500"):
        await call_tool("rag_kb", "search", {})

    assert calls["n"] == 3  # 业务可重试错重试 3 次
    breaker = mcp_mod._mcp_breakers.get("csmp:mcp_degraded:rag_kb")
    assert breaker.failures == 1  # 重试耗尽计一次熔断失败


@pytest.mark.asyncio
async def test_business_error_recovers_on_retry(monkeypatch, _isolate):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def transport(server, tool, payload, timeout_seconds):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return {"ok": True}

    set_mcp_transport(transport)

    assert await call_tool("rag_kb", "search", {}) == {"ok": True}
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_breaker_open_rejects_without_hitting_server(_isolate):
    breaker = mcp_mod._mcp_breakers.get_or_create("csmp:mcp_degraded:rag_kb")
    for _ in range(5):
        await breaker.record_failure()

    async def transport(server, tool, payload, timeout_seconds):
        raise AssertionError("should not be called")

    set_mcp_transport(transport)

    with pytest.raises(MCPCircuitOpenError, match="rag_kb"):
        await call_tool("rag_kb", "search", {})


@pytest.mark.asyncio
async def test_one_server_breaker_does_not_affect_another(_isolate):
    breaker = mcp_mod._mcp_breakers.get_or_create("csmp:mcp_degraded:rag_kb")
    for _ in range(5):
        await breaker.record_failure()

    async def transport(server, tool, payload, timeout_seconds):
        return {"server": server}

    set_mcp_transport(transport)

    with pytest.raises(MCPCircuitOpenError):
        await call_tool("rag_kb", "search", {})
    assert await call_tool("call_qc", "search", {}) == {"server": "call_qc"}


@pytest.mark.asyncio
async def test_csmp_mcp_open_does_not_affect_qc_llm(_isolate):
    """批次验收3：csmp:mcp_degraded:rag_kb 熔断不影响 qc:llm_degraded:* 调用。"""
    breaker = mcp_mod._mcp_breakers.get_or_create("csmp:mcp_degraded:rag_kb")
    for _ in range(5):
        await breaker.record_failure()

    async def mcp_transport(server, tool, payload, timeout_seconds):
        raise AssertionError("should not be called")

    set_mcp_transport(mcp_transport)
    with pytest.raises(MCPCircuitOpenError):
        await call_tool("rag_kb", "search", {})

    # 切到 qc 服务的 LLM 调用不受 csmp MCP 熔断影响（无全局单 key）
    set_default_service("qc")

    async def llm_transport(model, messages, timeout_seconds):
        return f"{model} ok"

    set_llm_transport(llm_transport)
    result = await invoke_llm("qwen-72b", [])
    assert result["content"] == "qwen-72b ok"


@pytest.mark.asyncio
async def test_transport_not_registered_raises(_isolate):
    with pytest.raises(RuntimeError, match="set_mcp_transport"):
        await call_tool("rag_kb", "search", {})


def test_mcp_total_below_llm_total():
    # 超时层级约束：MCP total(30s) < LLM total(120s)
    assert Timeout(total=30.0).total < Timeout().total
