"""Batch 3（任务 4.1-4.5）网关可靠性层验收测试。

覆盖：
- 4.1 per-server 熔断（连续 5 失败 → 第 6 次 MCPCircuitOpenError；per-server 隔离；
  half-open 探针成功闭合/失败重开；注册表 fail_max=5 / reset_timeout=30s）
- 4.2 超时与错误分级（30s total 超时不重试+计失败+标降级；权限拒绝不重试不计失败；
  业务错重试 3 次计 1 次失败后 fallback 兜默认）
- 4.3 per-server 限流（429 + csmp.rate_limit.hit；Redis 故障 fail-open + degraded 事件；
  per-server 隔离）
- 4.4 重试单层 + 5 工具下游 5xx 容错（返空/默认，不抛异常；请求计数=3 证明单层；
  data_platform 回写失败零丢失入队 csmp:backflow:retry）
- 4.5 超时层级 MCP total=30s < LLM total=120s
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from redis.exceptions import RedisError

from common.mcp import reliability as mcp_mod
from common.mcp.reliability import MCPCircuitOpenError
from common.resilience._redis import configure_redis
from common.resilience.timeout import LLM_TOTAL_TIMEOUT, MCP_TOTAL_TIMEOUT, Timeout

from app import mcp_server, qc_mcp_client
from app import audit
from app.bridge_http import BridgeHttpClient
from app.config import settings
from app.mcp_errors import MCPError, MCPPermissionDeniedError, MCPTimeoutError
from app.rate_limit import MCPRateLimitMiddleware
from app.reliability import call_tool_reliable
from app.tools import TOOL_ROUTES, ToolRoute, _case_fixture_fallback, _empty_list_fallback
from app.tools.business_ticket import BusinessTicketToolset
from app.tools.call_qc_asr import CallQcAsrToolset
from app.tools.data_platform import DataPlatformToolset
from app.tools.rag_kb import RagKbToolset


# ---------- 4.1 per-server 熔断 ----------


async def test_4_1_breaker_opens_after_5_failures_and_rejects_6th(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def failing(params, *, trace_id=""):
        calls["n"] += 1
        raise MCPError("rag_kb", "search_regulations", "downstream 500")

    monkeypatch.setitem(
        TOOL_ROUTES["rag_kb"],
        "search_regulations",
        ToolRoute(handler=failing, fallback=_empty_list_fallback),
    )

    # 连续 5 次调用：每次业务错重试 3 次耗尽 → 容错返空 + 熔断计 1 次失败
    for _ in range(5):
        result = await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
        assert result == []
    assert calls["n"] == 15  # 5 次调用 × 3 次重试

    # 第 6 次：熔断 open，类型化错误拒绝且不触达 server
    with pytest.raises(MCPCircuitOpenError, match="rag_kb"):
        await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
    assert calls["n"] == 15  # open 后未再触达下游


async def test_4_1_breaker_open_per_server_isolation(isolate, monkeypatch):
    breaker = mcp_mod._mcp_breakers.get_or_create("csmp:mcp_degraded:rag_kb")
    for _ in range(5):
        await breaker.record_failure()
    assert breaker.state == "open"

    async def ok_handler(params, *, trace_id=""):
        return [{"ok": True}]

    monkeypatch.setitem(
        TOOL_ROUTES["call_qc"],
        "query_call_asr",
        ToolRoute(handler=ok_handler, fallback=_empty_list_fallback),
    )

    # rag_kb open 拒绝；call_qc 不受影响（per-server 隔离）
    with pytest.raises(MCPCircuitOpenError):
        await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
    assert await mcp_server.dispatch("call_qc", "query_call_asr", {"case_id": "c1"}) == [
        {"ok": True}
    ]


async def test_4_1_half_open_probe_closes_on_success(isolate):
    clock = {"now": 0.0}
    breaker = mcp_mod._mcp_breakers.get_or_create(
        "csmp:mcp_degraded:rag_kb", clock=lambda: clock["now"]
    )
    for _ in range(5):
        await breaker.record_failure()
    assert breaker.state == "open"

    # 冷却期内仍拒绝
    assert await breaker.before_call() is False
    # 30s 后 half-open 放 1 探针；并发第二路按 open 拒绝
    clock["now"] = 31.0
    assert await breaker.before_call() is True
    assert breaker.state == "half_open"
    assert await breaker.before_call() is False
    # 探针成功 → closed
    await breaker.record_success()
    assert breaker.state == "closed"


async def test_4_1_half_open_probe_failure_reopens(isolate):
    clock = {"now": 0.0}
    breaker = mcp_mod._mcp_breakers.get_or_create(
        "csmp:mcp_degraded:call_qc", clock=lambda: clock["now"]
    )
    for _ in range(5):
        await breaker.record_failure()
    clock["now"] = 31.0
    assert await breaker.before_call() is True
    # 探针失败 → 重开 open 并重置计时
    await breaker.record_failure()
    assert breaker.state == "open"
    clock["now"] = 55.0
    assert await breaker.before_call() is False  # 重置计时后 30s 未到（31+30=61）
    clock["now"] = 61.0
    assert await breaker.before_call() is True  # 冷却期满再放探针


def test_4_1_breaker_registry_config(isolate):
    breaker = mcp_mod._mcp_breakers.get_or_create("csmp:mcp_degraded:rag_kb")
    assert breaker.fail_max == 5
    assert breaker.reset_timeout == 30
    assert breaker.key == "csmp:mcp_degraded:rag_kb"


# ---------- 4.2 超时与错误分级 ----------


async def test_4_2_total_timeout_not_retried_marks_degraded(isolate, monkeypatch):
    calls = {"n": 0}

    async def slow(params, *, trace_id=""):
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return []

    # transport 经 TOOL_ROUTES 分发：注入测试 handler 需 patch 路由表
    monkeypatch.setitem(
        TOOL_ROUTES["rag_kb"],
        "search_regulations",
        ToolRoute(handler=slow, fallback=_empty_list_fallback),
    )
    route = TOOL_ROUTES["rag_kb"]["search_regulations"]
    with pytest.raises(MCPTimeoutError):
        await call_tool_reliable(
            "rag_kb",
            "search_regulations",
            route,
            {"query": "q"},
            timeout=Timeout(total=0.05),
        )
    assert calls["n"] == 1  # 超时不重试
    breaker = mcp_mod._mcp_breakers.get("csmp:mcp_degraded:rag_kb")
    assert breaker is not None and breaker.failures == 1  # 计熔断失败
    assert "csmp:mcp_degraded:rag_kb" in isolate.redis.store  # 标降级


async def test_4_2_permission_denied_no_retry_no_count(isolate, monkeypatch):
    calls = {"n": 0}

    async def denied(params, *, trace_id=""):
        calls["n"] += 1
        raise MCPPermissionDeniedError("rag_kb", "search_regulations", "unauthorized")

    monkeypatch.setitem(
        TOOL_ROUTES["rag_kb"],
        "search_regulations",
        ToolRoute(handler=denied, fallback=_empty_list_fallback),
    )
    route = TOOL_ROUTES["rag_kb"]["search_regulations"]
    with pytest.raises(MCPPermissionDeniedError):
        await call_tool_reliable("rag_kb", "search_regulations", route, {"query": "q"})
    assert calls["n"] == 1  # 不重试，直接终止
    breaker = mcp_mod._mcp_breakers.get("csmp:mcp_degraded:rag_kb")
    assert breaker is not None and breaker.failures == 0  # 不计熔断失败
    assert "csmp:mcp_degraded:rag_kb" not in isolate.redis.store  # 不标降级


async def test_4_2_business_error_retries_3_then_fallback(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def flaky(params, *, trace_id=""):
        calls["n"] += 1
        raise MCPError("rag_kb", "search_regulations", "downstream 500")

    monkeypatch.setitem(
        TOOL_ROUTES["rag_kb"],
        "search_regulations",
        ToolRoute(handler=flaky, fallback=_empty_list_fallback),
    )
    route = TOOL_ROUTES["rag_kb"]["search_regulations"]
    result = await call_tool_reliable("rag_kb", "search_regulations", route, {"query": "q"})
    assert result == []  # fallback 兜默认，不抛异常转人审
    assert calls["n"] == 3  # 业务可重试错重试 3 次
    breaker = mcp_mod._mcp_breakers.get("csmp:mcp_degraded:rag_kb")
    assert breaker is not None and breaker.failures == 1  # 耗尽计 1 次熔断失败


async def test_4_2_business_error_recovers_on_retry(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def flaky(params, *, trace_id=""):
        calls["n"] += 1
        if calls["n"] < 3:
            raise MCPError("rag_kb", "search_regulations", "transient")
        return [{"doc_id": "d1"}]

    monkeypatch.setitem(
        TOOL_ROUTES["rag_kb"],
        "search_regulations",
        ToolRoute(handler=flaky, fallback=_empty_list_fallback),
    )
    route = TOOL_ROUTES["rag_kb"]["search_regulations"]
    result = await call_tool_reliable("rag_kb", "search_regulations", route, {"query": "q"})
    assert result == [{"doc_id": "d1"}]
    assert calls["n"] == 3
    breaker = mcp_mod._mcp_breakers.get("csmp:mcp_degraded:rag_kb")
    assert breaker is not None and breaker.failures == 0  # 成功不计失败


# ---------- 4.3 per-server 限流 ----------


def _rpc_body(tool_name: str, rpc_id: int = 1) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
        }
    ).encode()


async def _invoke_middleware(body: bytes) -> tuple[int, bytes, int]:
    """经 MCPRateLimitMiddleware 发一次 POST /mcp；返回 (status, body, 内层调用次数)。"""
    sent: list[dict] = []
    inner_calls = {"n": 0}

    async def inner(scope, receive, send):
        inner_calls["n"] += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {"type": "http.response.body", "body": b'{"jsonrpc":"2.0","id":1,"result":{}}'}
        )

    app = MCPRateLimitMiddleware(inner)
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    received = {"done": False}

    async def receive():
        if received["done"]:
            return {"type": "http.disconnect"}
        received["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    resp_body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return status, resp_body, inner_calls["n"]


async def test_4_3_rate_limit_429_and_hit_event(isolate, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_QPS", '{"rag_kb": 1}')
    body = _rpc_body("rag_kb.search_regulations")
    audit.reset_call_log()

    status, _, inner_n = await _invoke_middleware(body)
    assert status == 200 and inner_n == 1  # 令牌内放行

    status, resp_body, inner_n = await _invoke_middleware(body)
    assert status == 429  # 令牌耗尽拒绝
    assert inner_n == 0  # 未触达内层应用
    err = json.loads(resp_body)
    assert err["error"]["data"]["reason"] == "rate_limited"
    assert err["error"]["data"]["key"] == "csmp:mcp_ratelimit:rag_kb"

    names = [name for name, _ in isolate.events]
    assert "csmp.rate_limit.hit" in names

    # 整改 P2-4：429 边缘拒绝落 mcp_calls（status=error，含 server/tool/trace）
    entry = audit.get_call_log()[-1]
    assert entry["status"] == "error"
    assert entry["server"] == "rag_kb"
    assert entry["tool"] == "search_regulations"
    assert "rate_limited" in entry["error"]


async def test_4_3_rate_limit_per_server_isolation(isolate, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_QPS", '{"rag_kb": 1}')
    assert (await _invoke_middleware(_rpc_body("rag_kb.search_regulations")))[0] == 200
    assert (await _invoke_middleware(_rpc_body("rag_kb.search_regulations")))[0] == 429
    # business_ticket 不受 rag_kb 限流影响
    assert (await _invoke_middleware(_rpc_body("business_ticket.query_case_info")))[0] == 200


async def test_4_3_redis_failure_fail_open(isolate):
    class _BrokenRedis:
        async def set(self, *args, **kwargs):
            raise RedisError("redis down")

    configure_redis(_BrokenRedis())
    status, _, inner_n = await _invoke_middleware(_rpc_body("rag_kb.search_regulations"))
    assert status == 200 and inner_n == 1  # Redis 故障 fail-open 放行，不阻断

    names = [name for name, _ in isolate.events]
    assert "csmp.rate_limit.degraded" in names


async def test_4_3_non_tools_call_passthrough(isolate, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_QPS", '{"rag_kb": 0}')  # 0=不限流语义外的极端
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    status, _, inner_n = await _invoke_middleware(body)
    assert status == 200 and inner_n == 1


# ---------- 4.4 重试单层 + 5 工具下游 5xx 容错 ----------


def _counting_500_client(counter: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(500, json={"code": "B0001", "message": "boom"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_4_4_rag_kb_5xx_returns_empty_list(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    counter = {"n": 0}
    toolset = RagKbToolset("http://rag", timeout=0.5)
    toolset._http = BridgeHttpClient("http://rag", client=_counting_500_client(counter))
    monkeypatch.setitem(
        TOOL_ROUTES["rag_kb"],
        "search_regulations",
        ToolRoute(
            handler=lambda p, *, trace_id="": toolset.call_tool(
                "search_regulations", p, trace_id=trace_id
            ),
            fallback=_empty_list_fallback,
        ),
    )
    result = await call_tool_reliable(
        "rag_kb", "search_regulations", TOOL_ROUTES["rag_kb"]["search_regulations"], {"query": "q"}
    )
    assert result == []  # 返空列表，不抛异常转人审
    assert counter["n"] == 3  # 重试单层：3 次（非多重重试叠加）


async def test_4_4_business_ticket_5xx_returns_default_fixture(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    counter = {"n": 0}
    toolset = BusinessTicketToolset("http://dp")
    toolset._http = BridgeHttpClient("http://dp", client=_counting_500_client(counter))
    monkeypatch.setitem(
        TOOL_ROUTES["business_ticket"],
        "query_case_info",
        ToolRoute(
            handler=lambda p, *, trace_id="": toolset.call_tool(
                "query_case_info", p, trace_id=trace_id
            ),
            fallback=_case_fixture_fallback,
        ),
    )
    result = await call_tool_reliable(
        "business_ticket", "query_case_info", TOOL_ROUTES["business_ticket"]["query_case_info"], {"case_id": "C1"}
    )
    assert result["case_id"] == "C1"  # 默认 fixture（零值字段，不伪造）
    assert result["overdue_amount"] == 0.0
    assert counter["n"] == 3


async def test_4_4_data_platform_5xx_enqueues_and_returns_success_shape(
    isolate, monkeypatch
):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    counter = {"n": 0}
    toolset = DataPlatformToolset("http://dp")
    toolset._http = BridgeHttpClient("http://dp", client=_counting_500_client(counter))
    monkeypatch.setitem(
        TOOL_ROUTES["data_platform"],
        "write_session",
        ToolRoute(
            handler=lambda p, *, trace_id="": toolset.call_tool(
                "write_session", p, trace_id=trace_id
            ),
            fallback=lambda p, *, trace_id="", error=None: toolset.fallback(
                "write_session", p, trace_id=trace_id, error=error
            ),
        ),
    )
    result = await call_tool_reliable(
        "data_platform",
        "write_session",
        TOOL_ROUTES["data_platform"]["write_session"],
        {
            "session_id": "s1",
            "case_id": "c1",
            "complaint_id": "cp1",
            "task_status": "resolved",
            "verdict": "upheld",
        },
    )
    # 成功形状返回，工单正常推进（快照投诉列保持空）
    assert result == {
        "success": True,
        "persisted": False,
        "case_id": "c1",
        "complaint_id": "cp1",
    }
    assert counter["n"] == 3  # 单层重试：非源桥内环 3 × 外层 3 = 9 次叠加
    queued = isolate.redis.lists.get("csmp:backflow:retry", [])
    assert len(queued) == 1  # 零丢失入队
    payload = json.loads(queued[0])
    assert payload["error_code"] == "A5013"
    assert payload["session_id"] == "s1"


async def test_4_4_call_qc_asr_5xx_returns_empty_list(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    counter = {"n": 0}
    toolset = CallQcAsrToolset("http://dp")
    toolset._dp = BridgeHttpClient("http://dp", client=_counting_500_client(counter))
    monkeypatch.setitem(
        TOOL_ROUTES["call_qc"],
        "query_call_asr",
        ToolRoute(
            handler=lambda p, *, trace_id="": toolset.call_tool(
                "query_call_asr", p, trace_id=trace_id
            ),
            fallback=_empty_list_fallback,
        ),
    )
    result = await call_tool_reliable(
        "call_qc", "query_call_asr", TOOL_ROUTES["call_qc"]["query_call_asr"], {"case_id": "c1"}
    )
    assert result == []
    assert counter["n"] == 3  # 每次尝试快照 GET 即 500，重试单层共 3 次


async def test_4_4_call_qc_violations_qc_mcp_5xx_returns_empty_list(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(500, json={"code": "B0001", "message": "boom"})

    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(qc_mcp_client.httpx, "AsyncClient", client_factory)
    route = TOOL_ROUTES["call_qc"]["query_qc_violations"]
    result = await call_tool_reliable(
        "call_qc",
        "query_qc_violations",
        route,
        {"case_id": "c1", "session_id": "s1"},  # 直带 session_id 跳过快照反查
    )
    assert result == []  # 容错兜空，图继续判
    assert counter["n"] == 3  # 重试单层：每次尝试 1 个 initialize POST


# ---------- 4.5 超时层级 ----------


def test_4_5_timeout_hierarchy_mcp_below_llm():
    # 层级约束：MCP total=30s < LLM invoke_llm total=120s（挂死的 MCP 先超时暴露）
    assert MCP_TOTAL_TIMEOUT == 30.0
    assert LLM_TOTAL_TIMEOUT == 120.0
    assert MCP_TOTAL_TIMEOUT < LLM_TOTAL_TIMEOUT
