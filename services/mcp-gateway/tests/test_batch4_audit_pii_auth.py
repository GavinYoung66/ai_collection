"""Batch 4（任务 5.1-5.4 / 6.1-6.2）审计 + PII 脱敏 + 鉴权验收测试。

覆盖：
- 5.1 mcp_calls 审计：成功路 status=success（latency_ms/trace_id），失败路
  （超时/熔断/业务错/权限/未知路由）status=error（error/trace_id），tools/list 同样审计
- 5.2 审计经 call_log_sink 以 channel=mcp emit，entry 字段对齐 mcp_calls JSONB
- 5.3 出口 PII 递归脱敏（姓名/手机号/身份证/银行卡，嵌套 dict/list 无明文）
- 5.4 明文 PII 触发 mcp_pii_leak_sanitized 事件；无 PII 不触发；best-effort 不阻断
- 6.1 ENABLE_AUTH=false 直通；true 时无凭证/畸形 Bearer 拒绝
- 6.2 鉴权失败 HTTP 401 + B0002（SHALL NOT B0007）；有效 API Key 放行
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime
from datetime import timezone as dt_tz
from types import SimpleNamespace

import httpx
import pytest
from jose import jwt as jose_jwt
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from common.config.settings import settings as common_settings
from common.mcp import reliability as mcp_mod
from common.mcp.reliability import MCPCircuitOpenError

from app import audit, mcp_server
from app.auth import MCPAuthMiddleware
from app.call_log_sink import TempFileCallLogSink, set_call_log_sink
from app.config import settings as gw_settings
from app.mcp_errors import MCPPermissionDeniedError
from app.tools import TOOL_ROUTES, ToolRoute, _empty_list_fallback


@pytest.fixture(autouse=True)
def reset_audit():
    audit.reset_call_log()
    audit.reset_fallback_error()
    set_call_log_sink(None)
    yield
    audit.reset_call_log()
    audit.reset_fallback_error()
    set_call_log_sink(None)


def _patch_route(monkeypatch, server="rag_kb", tool="search_regulations", handler=None, fallback=None):
    monkeypatch.setitem(
        TOOL_ROUTES[server],
        tool,
        ToolRoute(handler=handler, fallback=fallback or _empty_list_fallback),
    )


# ---------- 5.1 mcp_calls 审计日志 ----------


async def test_5_1_success_call_audited(isolate, monkeypatch):
    async def ok(params, *, trace_id=""):
        return [{"hit": "doc-1"}]

    _patch_route(monkeypatch, handler=ok)
    result = await mcp_server.dispatch(
        "rag_kb", "search_regulations", {"query": "合规", "top_k": 3},
        trace_id="trace-5-1", session_id="sess-5-1",
    )
    assert result == [{"hit": "doc-1"}]
    entries = audit.get_call_log()
    assert len(entries) == 1
    e = entries[-1]
    assert e["server"] == "rag_kb"
    assert e["tool"] == "search_regulations"
    assert e["status"] == "success"
    assert e["error"] is None
    assert e["trace_id"] == "trace-5-1"
    assert e["session_id"] == "sess-5-1"
    assert isinstance(e["latency_ms"], int) and e["latency_ms"] >= 0
    assert isinstance(e["started_at"], datetime) and isinstance(e["ended_at"], datetime)
    assert e["ended_at"] >= e["started_at"]
    # params 摘要：保留 key、value 截断
    assert e["params"]["query"] == "合规"
    assert e["params"]["top_k"] == "3"
    assert e["attempt"] == 1


async def test_5_1_business_error_fallback_audited_as_error(isolate, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_MCP_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def flaky(params, *, trace_id=""):
        calls["n"] += 1
        raise RuntimeError("downstream 500")

    _patch_route(monkeypatch, handler=flaky)
    # 容错：调用方仍收到空列表（图继续判），但审计记 status=error
    result = await mcp_server.dispatch(
        "rag_kb", "search_regulations", {"query": "q"}, trace_id="trace-biz"
    )
    assert result == []
    assert calls["n"] == 3
    e = audit.get_call_log()[-1]
    assert e["status"] == "error"
    assert e["error"].startswith("business_error")
    assert "downstream 500" in e["error"]
    assert e["trace_id"] == "trace-biz"


async def test_5_1_timeout_audited_as_error(isolate, monkeypatch):
    import app.reliability as rel

    monkeypatch.setattr(rel, "MCP_TOTAL_TIMEOUT", 0.05)

    async def slow(params, *, trace_id=""):
        await asyncio.sleep(0.2)
        return []

    _patch_route(monkeypatch, handler=slow)
    with pytest.raises(Exception, match="timeout"):
        await mcp_server.dispatch(
            "rag_kb", "search_regulations", {"query": "q"}, trace_id="trace-timeout"
        )
    e = audit.get_call_log()[-1]
    assert e["status"] == "error"
    assert e["error"] == "timeout"
    assert e["trace_id"] == "trace-timeout"


async def test_5_1_circuit_open_audited_as_error(isolate, monkeypatch):
    breaker = mcp_mod._mcp_breakers.get_or_create("csmp:mcp_degraded:rag_kb")
    for _ in range(5):
        await breaker.record_failure()
    assert breaker.state == "open"

    async def ok(params, *, trace_id=""):
        return [{"x": 1}]

    _patch_route(monkeypatch, handler=ok)
    with pytest.raises(MCPCircuitOpenError):
        await mcp_server.dispatch(
            "rag_kb", "search_regulations", {"query": "q"}, trace_id="trace-open"
        )
    e = audit.get_call_log()[-1]
    assert e["status"] == "error"
    assert e["error"] == "circuit_open"
    assert e["trace_id"] == "trace-open"


async def test_5_1_permission_denied_audited_as_error(isolate, monkeypatch):
    async def denied(params, *, trace_id=""):
        raise MCPPermissionDeniedError("rag_kb", "search_regulations", "unauthorized")

    _patch_route(monkeypatch, handler=denied)
    with pytest.raises(MCPPermissionDeniedError):
        await mcp_server.dispatch(
            "rag_kb", "search_regulations", {"query": "q"}, trace_id="trace-denied"
        )
    e = audit.get_call_log()[-1]
    assert e["status"] == "error"
    assert e["error"] == "permission_denied"
    assert e["trace_id"] == "trace-denied"


async def test_5_1_unknown_server_audited_as_error(isolate):
    with pytest.raises(mcp_server.UnknownServerError):
        await mcp_server.dispatch("nope", "whatever", {}, trace_id="trace-unknown")
    e = audit.get_call_log()[-1]
    assert e["status"] == "error"
    assert e["error"].startswith("unknown_route")
    assert e["trace_id"] == "trace-unknown"


async def test_5_1_list_tools_audited(isolate):
    tools = await mcp_server.mcp.list_tools()
    assert len(tools) == 10  # 4 server 共 10 个静态注册工具
    entries = [e for e in audit.get_call_log() if e["tool"] == "list_tools"]
    assert len(entries) == 1
    e = entries[-1]
    assert e["server"] == ""
    assert e["status"] == "success"
    assert isinstance(e["latency_ms"], int)


# ---------- 5.2 审计经 call_log_sink emit，对齐 mcp_calls JSONB ----------


# 对齐 complaint_agent_logs.mcp_calls JSONB 条目字段（与 complaint-service
# MCPClient._record_audit 同源）
_MCP_CALLS_FIELDS = {
    "server": str,
    "tool": str,
    "params": (dict, type(None)),
    "trace_id": str,
    "session_id": str,
    "attempt": int,
    "latency_ms": int,
    "started_at": datetime,
    "ended_at": datetime,
    "status": str,
    "error": (str, type(None)),
}


async def test_5_2_sink_emits_channel_mcp_with_jsonb_shape(isolate, monkeypatch, tmp_path):
    sink_path = tmp_path / "mcp_calls.jsonl"
    set_call_log_sink(TempFileCallLogSink(sink_path))

    async def ok(params, *, trace_id=""):
        return [{"ok": True}]

    _patch_route(monkeypatch, handler=ok)
    await mcp_server.dispatch(
        "rag_kb", "search_regulations", {"query": "q"}, trace_id="trace-sink"
    )

    rows = [json.loads(line) for line in sink_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["channel"] == "mcp"
    for field in _MCP_CALLS_FIELDS:
        assert field in row, f"mcp_calls JSONB 缺字段: {field}"
    assert row["status"] == "success"
    assert row["trace_id"] == "trace-sink"
    assert isinstance(row["latency_ms"], int)
    # 落盘后 datetime 已 isoformat 序列化
    assert isinstance(row["started_at"], str) and isinstance(row["ended_at"], str)
    datetime.fromisoformat(row["started_at"])
    datetime.fromisoformat(row["ended_at"])


async def test_5_2_memory_entry_matches_jsonb_field_types(isolate, monkeypatch):
    async def ok(params, *, trace_id=""):
        return []

    _patch_route(monkeypatch, handler=ok)
    await mcp_server.dispatch("call_qc", "query_call_asr", {"case_id": "c1"})
    entry = audit.get_call_log()[-1]
    for field, expected_type in _MCP_CALLS_FIELDS.items():
        assert field in entry
        assert isinstance(entry[field], expected_type), (field, type(entry[field]))
    assert entry["status"] in ("success", "error")


async def test_5_2_no_sink_registered_is_noop(isolate, monkeypatch):
    # 默认无 sink：emit 不抛异常，审计仍进进程内快照
    set_call_log_sink(None)

    async def ok(params, *, trace_id=""):
        return []

    _patch_route(monkeypatch, handler=ok)
    await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
    assert len(audit.get_call_log()) == 1


async def test_5_2_sink_failure_does_not_block_call(isolate, monkeypatch):
    class BrokenSink:
        def write(self, channel, entry):
            raise RuntimeError("sink disk broken")

    set_call_log_sink(BrokenSink())

    async def ok(params, *, trace_id=""):
        return [{"fine": True}]

    _patch_route(monkeypatch, handler=ok)
    # sink 异常被 best-effort 吞掉：调用正常返回、审计仍记录
    result = await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
    assert result == [{"fine": True}]
    assert audit.get_call_log()[-1]["status"] == "success"


# ---------- 5.3 出口 PII 递归脱敏 ----------

_PII_PAYLOAD = {
    "case_id": "c1",
    "customer_name": "张三",
    "records": [
        {"content": "客户手机号13800138000请回拨", "remark": "身份证110101199001011234留档"},
        {"bank": "卡号6222021234567890123已核实"},
    ],
    "nested": [{"contacts": [{"name": "欧阳修"}]}],
    "clean": "无敏感信息",
}


async def test_5_3_dispatch_strips_all_plaintext_pii(isolate, monkeypatch):
    async def leaky(params, *, trace_id=""):
        return _PII_PAYLOAD

    _patch_route(monkeypatch, server="business_ticket", tool="query_case_info", handler=leaky)
    result = await mcp_server.dispatch(
        "business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="trace-pii"
    )
    blob = json.dumps(result, ensure_ascii=False)
    # 四类明文 PII 全部不得出现在出口
    assert "13800138000" not in blob
    assert "110101199001011234" not in blob
    assert "6222021234567890123" not in blob
    assert "张三" not in blob
    assert "欧阳修" not in blob
    # 脱敏后形态可核验
    assert "138****8000" in blob
    assert result["customer_name"] == "张*"
    assert result["nested"][0]["contacts"][0]["name"] == "欧**"
    # 非 PII 内容保持可用
    assert result["clean"] == "无敏感信息"
    assert result["case_id"] == "c1"


def test_5_3_sanitize_recursive_unit():
    from app import pii

    clean = pii.sanitize(_PII_PAYLOAD)
    assert pii.contains_pii(clean) is False
    blob = json.dumps(clean, ensure_ascii=False)
    for plaintext in ("13800138000", "110101199001011234", "6222021234567890123", "张三", "欧阳修"):
        assert plaintext not in blob


def test_5_3_sanitize_does_not_mutate_input():
    from app import pii

    original = {"customer_name": "张三"}
    sanitized = pii.sanitize(original)
    assert sanitized == {"customer_name": "张*"}
    assert original == {"customer_name": "张三"}  # 入参保持原样


def test_5_3_non_person_name_keys_not_over_masked():
    from app import pii

    # 有界白名单：rule_name/template_name 等非人名字段不做姓名脱敏，避免误伤 RAG 字段
    data = {"rule_name": "张三", "template_name": "李四"}
    assert pii.contains_pii(data) is False
    assert pii.sanitize(data) == {"rule_name": "张三", "template_name": "李四"}


def test_5_3_plain_string_and_list_forms():
    from app import pii

    assert "13800138000" not in pii.sanitize("电话13800138000")
    assert pii.sanitize(["13800138000", "ok"]) == ["138****8000", "ok"]
    # 数字/布尔/None 原样返回
    assert pii.sanitize({"n": 1, "b": True, "x": None}) == {"n": 1, "b": True, "x": None}


def test_5_3_integer_and_tuple_pii_sanitized():
    """整改 P1-2：JSON number 型手机/身份证 + tuple 容器同样无明文残留。"""
    from app import pii

    payload = {
        "phone_int": 13800138000,
        "tup": ("电话13800138000", 110101199001011234),
        "flag": True,
        "ordinary_n": 12345,
    }
    assert pii.contains_pii(payload) is True
    out = pii.sanitize(payload)
    blob = json.dumps(out, ensure_ascii=False, default=str)
    assert "13800138000" not in blob
    assert "110101199001011234" not in blob
    # 命中数字型 PII → 脱敏字符串；普通数字保型；bool 不参与数字判定
    assert out["phone_int"] == "138****8000"
    assert out["tup"][0] == "电话138****8000"
    # 18 位数字串内可能被手机号正则抢先匹配（common mask_text 链式顺序固有，
    # 字符串路径同形态）；脱敏断言只认「无明文 + 已变形」，不锁具体掩码样式
    assert "110101199001011234" not in out["tup"][1]
    assert "*" in out["tup"][1]
    assert isinstance(out["tup"], tuple)  # tuple 同构递归
    assert out["flag"] is True
    assert out["ordinary_n"] == 12345
    # 干净数字结构不误报
    assert pii.contains_pii({"n": 42, "t": (1, 2), "b": False}) is False
    # 入参未原地修改
    assert payload["phone_int"] == 13800138000


# ---------- 5.4 明文 PII 触发 mcp_pii_leak_sanitized 审计事件 ----------


async def test_5_4_pii_call_triggers_event_once(isolate, monkeypatch):
    calls = []

    def fake_log_action(action, **kwargs):
        calls.append({"action": action, **kwargs})

    monkeypatch.setattr(audit, "log_action", fake_log_action)

    async def leaky(params, *, trace_id=""):
        return _PII_PAYLOAD

    _patch_route(monkeypatch, server="business_ticket", tool="query_case_info", handler=leaky)
    await mcp_server.dispatch(
        "business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="trace-event"
    )
    assert len(calls) == 1
    event = calls[0]
    assert event["action"] == "mcp_pii_leak_sanitized"
    assert event["resource"] == "business_ticket/query_case_info"
    assert event["trace_id"] == "trace-event"


async def test_5_4_clean_call_does_not_trigger_event(isolate, monkeypatch):
    calls = []
    monkeypatch.setattr(audit, "log_action", lambda action, **kw: calls.append(action))

    async def clean(params, *, trace_id=""):
        return [{"content": "全部合规，无PII", "n": 1}]

    _patch_route(monkeypatch, handler=clean)
    await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
    assert calls == []


async def test_5_4_event_failure_is_best_effort(isolate, monkeypatch):
    def boom(action, **kwargs):
        raise RuntimeError("audit pipeline down")

    monkeypatch.setattr(audit, "log_action", boom)

    async def leaky(params, *, trace_id=""):
        return {"phone": "13800138000"}

    _patch_route(monkeypatch, handler=leaky)
    # 审计事件上报失败 SHALL NOT 阻断：调用照常返回脱敏结果
    result = await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
    assert "13800138000" not in json.dumps(result, ensure_ascii=False)
    assert result["phone"] == "138****8000"


# ---------- 6.1 / 6.2 网关鉴权（含整改 P2-1/P2-2/P2-4）----------


def _probe_app():
    """挂 MCPAuthMiddleware 的最小探测应用（/mcp + /health + /metrics）。"""

    async def mcp_stub(request):
        # 回显 body：验证鉴权放行路径的 body 单次重放正确
        return JSONResponse({"reached": "mcp", "echo": await request.json()})

    async def health_stub(request):  # noqa: ARG001
        return JSONResponse({"status": "healthy"})

    async def metrics_stub(request):  # noqa: ARG001
        return JSONResponse({"metrics": "ok"})

    star = Starlette(
        routes=[
            Route("/mcp", mcp_stub, methods=["POST"]),
            Route("/health", health_stub, methods=["GET"]),
            Route("/metrics", metrics_stub, methods=["GET"]),
        ]
    )
    star.add_middleware(MCPAuthMiddleware)
    return star


@pytest.fixture
def auth_client(monkeypatch):
    """开启鉴权 + 注入测试 API Key；产出 httpx transport。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(gw_settings, "AUTH_API_KEY", "ak-batch4-test-001")
    # 确保桥接生效：平台 API_KEYS 清空，由 MCP_GATEWAY_AUTH_API_KEY 回填
    monkeypatch.setattr(common_settings, "API_KEYS", "")
    return httpx.ASGITransport(app=_probe_app())


async def test_6_1_disabled_passes_through_without_credentials(isolate, monkeypatch):
    # 默认 ENABLE_AUTH=false：无凭证直通，不触发任何校验；body 完整重放
    assert gw_settings.ENABLE_AUTH is False
    transport = httpx.ASGITransport(app=_probe_app())
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/mcp", json=rpc)
    assert r.status_code == 200
    assert r.json() == {"reached": "mcp", "echo": rpc}


async def test_6_1_enabled_missing_credentials_rejected(isolate, auth_client):
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        # 完全无 Authorization 头
        r1 = await client.post("/mcp", json={})
        assert r1.status_code == 401
        # 非 Bearer 形态（Basic）→ missing credentials
        r2 = await client.post("/mcp", json={}, headers={"Authorization": "Basic xyz"})
        assert r2.status_code == 401
        # Bearer 前缀但 token 为空
        r3 = await client.post("/mcp", json={}, headers={"Authorization": "Bearer "})
        assert r3.status_code == 401


async def test_6_1_enabled_health_still_open(isolate, auth_client):
    # 鉴权开启时 /health 也不拦截（容器健康检查依赖，spec scenario）
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        r = await client.get("/health")
    assert r.status_code == 200


async def test_6_1_enabled_valid_api_key_passes(isolate, auth_client):
    rpc = {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        r = await client.post(
            "/mcp", json=rpc, headers={"Authorization": "Bearer ak-batch4-test-001"}
        )
    assert r.status_code == 200
    assert r.json() == {"reached": "mcp", "echo": rpc}  # body 重放无损
    # 桥接后 common settings 可见网关 key（authn 单一配置源）
    assert common_settings.API_KEYS == "ak-batch4-test-001"


async def test_6_1_enabled_wrong_api_key_rejected(isolate, auth_client):
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        r = await client.post(
            "/mcp", json={}, headers={"Authorization": "Bearer ak-wrong-key"}
        )
    assert r.status_code == 401


async def test_6_1_enabled_valid_jwt_passes(isolate, monkeypatch):
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(common_settings, "API_KEYS", "")
    # HS256 JWT：用 common settings 同一密钥签名（iss/aud 未配置 → 跳过校验）
    token = jose_jwt.encode(
        {
            "sub": "user-batch4",
            "role": "admin",
            "exp": datetime.now(dt_tz.utc).timestamp() + 3600,
        },
        common_settings.JWT_SECRET,
        algorithm=common_settings.JWT_ALGORITHM,
    )
    transport = httpx.ASGITransport(app=_probe_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


async def test_6_2_mcp_rejection_is_jsonrpc_b0002_not_b0007(isolate, auth_client):
    """整改 P2-1：/mcp 拒绝为 JSON-RPC error 信封（data.code=B0002），非统一信封。"""
    rpc = {
        "jsonrpc": "2.0",
        "id": 4242,
        "method": "tools/call",
        "params": {"name": "rag_kb.search_regulations", "arguments": {"query": "q"}},
    }
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        cases = [
            ("no header", {}),
            ("bad scheme", {"Authorization": "Basic abc"}),
            ("empty bearer", {"Authorization": "Bearer "}),
            ("wrong key", {"Authorization": "Bearer ak-wrong"}),
            ("forged jwt", {"Authorization": "Bearer " + "a.b.c"}),
        ]
        for label, headers in cases:
            r = await client.post("/mcp", json=rpc, headers=headers)
            assert r.status_code == 401, f"{label}: {r.status_code}"
            body = r.json()
            assert body["jsonrpc"] == "2.0"
            assert body["id"] == 4242  # JSON-RPC id 回显
            assert body["error"]["code"] == -32001
            assert body["error"]["data"]["code"] == "B0002", f"{label}: {body}"
            assert "code" not in body  # 未包装统一信封
            assert "B0007" not in r.text
        # 畸形/空 body：id 回退 null，仍返回 401/B0002
        r_bad = await client.post("/mcp", content=b"not-json", headers={"Content-Type": "application/json"})
        assert r_bad.status_code == 401
        assert r_bad.json()["error"]["data"]["code"] == "B0002"
        assert r_bad.json()["id"] is None


async def test_6_2_metrics_rejection_is_envelope_b0002(isolate, auth_client):
    """/metrics 非 MCP 端点：拒绝使用统一信封 B0002。"""
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        r = await client.get("/metrics")
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "B0002"
    assert body["data"] is None
    assert "trace_id" in body
    assert "B0007" not in r.text


async def test_p2_2_metrics_protected_when_auth_enabled(isolate, auth_client):
    """整改 P2-2：ENABLE_AUTH=true 时 /metrics 需凭证，/health 始终放行。"""
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/metrics")).status_code == 401
        r = await client.get(
            "/metrics", headers={"Authorization": "Bearer ak-batch4-test-001"}
        )
        assert r.status_code == 200


async def test_p2_4_auth_rejection_audited(isolate, auth_client):
    """整改 P2-4：401 边缘拒绝落 mcp_calls，trace_id 全链路串联。"""
    rpc_call = {
        "jsonrpc": "2.0",
        "id": 4242,
        "method": "tools/call",
        "params": {"name": "rag_kb.search_regulations", "arguments": {"query": "q"}},
    }
    async with httpx.AsyncClient(transport=auth_client, base_url="http://t") as client:
        audit.reset_call_log()
        r = await client.post(
            "/mcp", json=rpc_call, headers={"X-Trace-Id": "trace-authz-1"}
        )
        assert r.status_code == 401
        assert r.headers["x-trace-id"] == "trace-authz-1"
        entry = audit.get_call_log()[-1]
        assert entry["server"] == "rag_kb"
        assert entry["tool"] == "search_regulations"
        assert entry["status"] == "error"
        assert entry["error"].startswith("unauthenticated:")
        assert entry["trace_id"] == "trace-authz-1"

        # tools/list 拒绝记 list_tools
        audit.reset_call_log()
        await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
            headers={"X-Trace-Id": "trace-authz-2"},
        )
        entry_list = audit.get_call_log()[-1]
        assert entry_list["tool"] == "list_tools"
        assert entry_list["trace_id"] == "trace-authz-2"

        # /metrics 拒绝记 metrics
        audit.reset_call_log()
        await client.get("/metrics", headers={"X-Trace-Id": "trace-authz-3"})
        entry_metrics = audit.get_call_log()[-1]
        assert entry_metrics["tool"] == "metrics"
        assert entry_metrics["trace_id"] == "trace-authz-3"


# 10 个线网工具的最小合法入参（data_platform/call_qc 的 session_id 是业务参数，
# 与 _meta.session_id 审计列语义不同，刻意取不同值验证来源）
_META_TOOL_CASES = [
    ("rag_kb.search_regulations", {"query": "q"}),
    ("rag_kb.search_response_templates", {"query": "q"}),
    ("business_ticket.query_case_info", {"case_id": "c1"}),
    ("business_ticket.query_history_tickets", {"case_id": "c1"}),
    ("business_ticket.query_history_complaints", {"case_id": "c1"}),
    ("data_platform.write_session", {"session_id": "biz-sess"}),
    ("data_platform.write_human_review_truth", {"session_id": "biz-sess"}),
    ("call_qc.query_call_asr", {"case_id": "c1"}),
    ("call_qc.query_qc_violations", {"case_id": "c1", "session_id": "biz-sess"}),
    ("call_qc.query_qc_alerts", {"case_id": "c1", "session_id": "biz-sess"}),
]


async def test_p1_1_all_ten_tools_propagate_meta_trace_and_session(isolate, monkeypatch):
    """整改 P1-1：全部 10 个工具的审计 trace_id/session_id 均来自 _meta。"""
    captured: dict[tuple[str, str], tuple[str, str]] = {}

    async def fake_reliable(server, tool, route, params, *, trace_id="", session_id="", **_kw):
        captured[(server, tool)] = (trace_id, session_id)
        return []

    monkeypatch.setattr(mcp_server, "call_tool_reliable", fake_reliable)
    # request_context 是 property；真 ContextVar 是 lowlevel 模块级 request_ctx
    from mcp.server.lowlevel.server import request_ctx
    fake_rc = SimpleNamespace(
        meta=SimpleNamespace(trace_id="meta-trace-X", session_id="meta-sess-X")
    )
    token = request_ctx.set(fake_rc)
    try:
        for name, args in _META_TOOL_CASES:
            await mcp_server.mcp.call_tool(name, dict(args))
    finally:
        request_ctx.reset(token)

    assert len(captured) == 10
    bad = {k: v for k, v in captured.items() if v != ("meta-trace-X", "meta-sess-X")}
    assert not bad, f"以下工具未透传 _meta trace/session: {bad}"


async def test_p1_1_missing_meta_falls_back_to_empty(isolate, monkeypatch):
    """无 _meta 时不报错：trace 回退 HTTP 头（测试外为空），session 为空串。"""
    captured: dict[tuple[str, str], tuple[str, str]] = {}

    async def fake_reliable(server, tool, route, params, *, trace_id="", session_id="", **_kw):
        captured[(server, tool)] = (trace_id, session_id)
        return []

    monkeypatch.setattr(mcp_server, "call_tool_reliable", fake_reliable)
    from mcp.server.lowlevel.server import request_ctx
    token = request_ctx.set(SimpleNamespace(meta=None))
    try:
        await mcp_server.mcp.call_tool("rag_kb.search_regulations", {"query": "q"})
    finally:
        request_ctx.reset(token)
    assert captured[("rag_kb", "search_regulations")] == ("", "")


def test_p2_3_meta_trace_id_extractor():
    """整改 P2-3：_meta trace_id 提取容错（list_tools 审计复用同一 helper）。"""
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(trace_id="meta-trace-9"))
    )
    assert mcp_server._trace_id_from_ctx(ctx) == "meta-trace-9"
    # 请求外调用（request_context=None）/ meta 缺失 → 空串，由 HTTP 头兜底
    assert mcp_server._trace_id_from_ctx(SimpleNamespace(request_context=None)) == ""
    assert mcp_server._trace_id_from_ctx(SimpleNamespace(request_context=SimpleNamespace(meta=None))) == ""


@contextlib.asynccontextmanager
async def _real_app_lifespan(application):
    """真实网关 app 的原生 ASGI lifespan 握手（MCP session manager 依赖启动）。"""
    rx: asyncio.Queue = asyncio.Queue()
    tx: asyncio.Queue = asyncio.Queue()

    async def _receive():
        return await rx.get()

    async def _send(message):
        await tx.put(message)

    task = asyncio.create_task(
        application({"type": "lifespan", "asgi": {"version": "3.0"}}, _receive, _send)
    )
    await rx.put({"type": "lifespan.startup"})
    started = await tx.get()
    assert started["type"] == "lifespan.startup.complete", started
    try:
        yield
    finally:
        await rx.put({"type": "lifespan.shutdown"})
        stopped = await tx.get()
        assert stopped["type"] == "lifespan.shutdown.complete", stopped
        await task


async def test_6_2_real_app_disabled_does_not_block_mcp(isolate):
    """真实网关应用集成：默认关闭鉴权时 POST /mcp 不返回 401/B0002；/health 200。"""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with _real_app_lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["code"] == "0"
            # 未初始化的裸 MCP 请求可能被协议层拒绝（4xx），但 SHALL NOT 是鉴权拒绝
            r = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert r.status_code != 401
            assert "B0002" not in r.text
            # /metrics 关闭鉴权时直接可达（Prometheus 原生格式）
            metrics = await client.get("/metrics")
            assert metrics.status_code == 200
            assert metrics.headers["content-type"].startswith("text/plain")
            assert "mcp_gateway_requests_total" in metrics.text
            assert "code" not in metrics.text[:200]  # 未包统一信封
