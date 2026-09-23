"""批次③ 进程内桥单测（mcp-real-path）。

用 httpx.MockTransport 模拟 data-platform / qc-service，验证：
- 三桥 fixture 形状对齐、查无返空列表不抛
- business_ticket 快照 404 fallback public.cases 且客户名脱敏
- data_platform 回写成功 / 连续失败 3 次入 csmp:backflow:retry 不抛
- RealMCPTransport 三 server 不依赖 MCP_GATEWAY_URL
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.mcp.business_ticket_bridge import BusinessTicketBridge
from app.mcp.call_qc_bridge import (
    CallQcBridge,
    map_qc_record_to_violations_fixture,
    map_qc_source_to_asr_fixture,
)
from app.mcp.data_platform_bridge import DataPlatformBridge
from app.mcp.real_transport import RealMCPTransport
from app.utils.redis_keys import backflow_retry_key

_CASE_ID = "11111111-2222-4333-8444-555555555501"
_SESSION_ID = "min-loop-sess-001"


def _envelope(data=None, code="0"):
    return {"code": code, "message": "ok", "data": data, "trace_id": "t1"}


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}

    async def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)
        return len(self.lists[k])


# ── business_ticket ──


@pytest.mark.asyncio
async def test_business_case_info_from_snapshot_fixture_shape():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/data/snapshot/{_CASE_ID}"
        return httpx.Response(
            200,
            json=_envelope(
                {
                    "case_id": _CASE_ID,
                    "customer_name": "张*",
                    "overdue_amount": 5000.0,
                    "overdue_days": 30,
                    "session_id": _SESSION_ID,
                }
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    bridge = BusinessTicketBridge("http://dp.test", client=client)
    out = await bridge.call_tool("query_case_info", {"case_id": _CASE_ID})
    await client.aclose()

    assert set(out.keys()) == {
        "case_id", "overdue_amount", "overdue_days", "repayment_records",
        "customer_name", "product_type", "create_time",
    }
    assert out["case_id"] == _CASE_ID
    assert out["overdue_amount"] == 5000.0
    assert out["overdue_days"] == 30
    assert out["repayment_records"] == []
    assert out["customer_name"] == "张*"
    # 快照表无此两列：不伪造
    assert out["product_type"] == ""
    assert out["create_time"] is None


@pytest.mark.asyncio
async def test_business_history_tools_return_empty_list():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    bridge = BusinessTicketBridge("http://dp.test", client=client)
    assert await bridge.call_tool("query_history_tickets", {"case_id": _CASE_ID}) == []
    assert await bridge.call_tool("query_history_complaints", {"case_id": _CASE_ID}) == []
    await client.aclose()


class _FakeMappings:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _FakeMappings(self._row)


class _FakeDbSession:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params):
        return _FakeResult(self._row)


@pytest.mark.asyncio
async def test_business_fallback_cases_masks_name(monkeypatch):
    """快照 404 → fallback public.cases；明文客户名出桥必须脱敏。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json=_envelope(None, code="B0004"))

    row = {
        "case_id": _CASE_ID,
        "customer_name": "张三丰",
        "overdue_amount": Decimal("1234.56"),
        "overdue_days": 7,
        "create_time": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    }
    monkeypatch.setattr(
        "common.db.session.async_session_factory", lambda: _FakeDbSession(row)
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    bridge = BusinessTicketBridge("http://dp.test", client=client)
    out = await bridge.call_tool("query_case_info", {"case_id": _CASE_ID})
    await client.aclose()

    assert out["customer_name"] == "张**"
    assert out["overdue_amount"] == 1234.56
    assert out["overdue_days"] == 7
    assert out["product_type"] == ""
    assert out["create_time"].endswith("+08:00")


# ── call_qc ──


def test_map_qc_source_to_asr_fixture():
    source = {
        "session_id": _SESSION_ID,
        "started_at": "2026-08-15T23:05:00+08:00",
        "duration_seconds": 180,
        "turns": [
            {"turn_id": "t1", "speaker": "agent", "text": "中文内容A"},
            {"turn_id": "t2", "speaker": "user", "text": "中文内容B"},
        ],
    }
    out = map_qc_source_to_asr_fixture(source)
    assert len(out) == 1
    seg = out[0]
    assert seg["call_session_id"] == _SESSION_ID
    assert seg["call_duration"] == 180
    assert seg["agent_role"] == "agent"
    assert seg["asr_text_with_timestamp"][0]["speaker"] == "agent"
    assert seg["asr_text_with_timestamp"][0]["text"] == "中文内容A"
    # qc-source turns 无逐句时间戳：ts 必须为空串而非编造
    assert seg["asr_text_with_timestamp"][0]["ts"] == ""


def test_map_qc_source_zero_duration_returns_empty():
    assert map_qc_source_to_asr_fixture({"session_id": "s", "duration_seconds": 0}) == []
    assert map_qc_source_to_asr_fixture({"session_id": "s"}) == []


@pytest.mark.asyncio
async def test_call_qc_asr_success_and_miss():
    routes = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/qc-source"):
            return httpx.Response(200, json=_envelope(routes["source"]))
        if "/snapshot/" in request.url.path:
            return httpx.Response(200, json=_envelope(routes["snapshot"]))
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    bridge = CallQcBridge("http://dp.test", "http://qc.test", client=client)

    routes["snapshot"] = {"case_id": _CASE_ID, "session_id": _SESSION_ID}
    routes["source"] = {
        "session_id": _SESSION_ID,
        "started_at": "2026-08-15T23:05:00+08:00",
        "duration_seconds": 60,
        "turns": [{"turn_id": "t1", "speaker": "agent", "text": "你好"}],
    }
    out = await bridge.call_tool("query_call_asr", {"case_id": _CASE_ID})
    assert len(out) == 1 and out[0]["call_duration"] == 60

    # 快照查不到 session_id → 空列表，不抛
    routes["snapshot"] = {"case_id": _CASE_ID, "session_id": None}
    assert await bridge.call_tool("query_call_asr", {"case_id": _CASE_ID}) == []
    await client.aclose()


def test_map_qc_record_to_violations_fixture():
    detail = {
        "qc_record_id": "qc-uuid-1",
        "session_id": _SESSION_ID,
        "hits": [
            {"rule_id": "RULE-001", "redline_tag": "threat", "evidence_text": "片段"},
        ],
    }
    out = map_qc_record_to_violations_fixture(detail)
    assert len(out) == 1
    item = out[0]
    assert set(item.keys()) == {
        "qc_id", "violation_type", "violation_rule_id", "call_session_id",
        "start_ts", "end_ts", "snippet_text", "score",
    }
    assert item["qc_id"] == "qc-uuid-1"
    assert item["violation_type"] == "threat"
    assert item["violation_rule_id"] == "RULE-001"
    assert item["call_session_id"] == _SESSION_ID
    assert item["snippet_text"] == "片段"
    assert item["start_ts"] == "" and item["end_ts"] == ""
    assert item["score"] == 0.0
    assert map_qc_record_to_violations_fixture({"session_id": "s", "hits": []}) == []


@pytest.mark.asyncio
async def test_call_qc_violations_record_miss_returns_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "/records/" in request.url.path:
            return httpx.Response(404, json={"code": "A2005", "data": None})
        return httpx.Response(
            200, json=_envelope({"case_id": _CASE_ID, "session_id": _SESSION_ID})
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    bridge = CallQcBridge("http://dp.test", "http://qc.test", client=client)
    out = await bridge.call_tool("query_qc_violations", {"case_id": _CASE_ID})
    await client.aclose()
    assert out == []


# ── data_platform 回写 ──


async def _resolver_ok(session_id):
    return (_CASE_ID, _CASE_ID)  # complaint_id, case_id（闭环里二者相同）


@pytest.mark.asyncio
async def test_write_session_puts_complaint_cols():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_envelope({"case_id": _CASE_ID}))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    redis = _FakeRedis()
    bridge = DataPlatformBridge(
        "http://dp.test",
        client=client,
        resolver=_resolver_ok,
        redis_client=redis,
    )
    out = await bridge.call_tool(
        "write_session",
        {"session_id": "sid-1", "task_status": "resolved", "verdict": "violation"},
    )
    await client.aclose()

    assert out == {"success": True}
    assert seen["path"] == f"/api/v1/data/snapshot/{_CASE_ID}"
    assert seen["body"] == {
        "complaint_id": _CASE_ID,
        "verdict": "violation",
        "task_status": "resolved",
    }
    assert redis.lists == {}  # 成功不入重试队列


@pytest.mark.asyncio
async def test_write_human_review_truth_same_cols_no_new_column():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_envelope({}))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    bridge = DataPlatformBridge(
        "http://dp.test", client=client, resolver=_resolver_ok, redis_client=_FakeRedis()
    )
    out = await bridge.call_tool(
        "write_human_review_truth",
        {"session_id": "sid-1", "task_status": "resolved", "verdict": "compliant"},
    )
    await client.aclose()
    assert out == {"success": True}
    # 与 write_session 相同的三列；快照表无独立人审列
    assert set(seen["body"].keys()) == {"complaint_id", "verdict", "task_status"}


@pytest.mark.asyncio
async def test_write_failure_enqueues_backflow_retry_and_never_raises():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    redis = _FakeRedis()
    bridge = DataPlatformBridge(
        "http://dp.test",
        client=client,
        resolver=_resolver_ok,
        redis_client=redis,
        max_retry=3,
    )
    out = await bridge.call_tool(
        "write_session",
        {"session_id": "sid-9", "task_status": "resolved", "verdict": "violation"},
    )
    await client.aclose()

    assert out == {"success": True}  # 不抛异常 → 工单正常推进
    assert calls["n"] == 3
    queue = redis.lists[backflow_retry_key()]
    assert len(queue) == 1
    import json

    payload = json.loads(queue[0])
    assert payload["error_code"] == "A5013"
    assert payload["kind"] == "session"
    assert payload["session_id"] == "sid-9"
    assert set(payload["data"].keys()) == {"complaint_id", "verdict", "task_status"}


@pytest.mark.asyncio
async def test_real_transport_three_servers_no_gateway_required():
    """USE_MOCK_MCP=false：三 server 走进程内桥，不抛 MCP_GATEWAY_URL missing。"""
    t = RealMCPTransport(
        gateway_url="",
        data_platform_url="http://dp.test",
        qc_service_url="http://qc.test",
        business_bridge=_StubBridge([]),
        call_qc_bridge=_StubBridge([]),
        data_platform_bridge=_StubBridge({"success": True}),
    )
    assert await t.call("business_ticket", "query_history_tickets", {}) == []
    assert await t.call("call_qc", "query_call_asr", {"case_id": "c"}) == []
    assert await t.call("data_platform", "write_session", {"session_id": "s"}) == {
        "success": True
    }


class _StubBridge:
    def __init__(self, value):
        self._value = value

    async def call_tool(self, tool, params, *, trace_id="", session_id=""):
        return self._value
