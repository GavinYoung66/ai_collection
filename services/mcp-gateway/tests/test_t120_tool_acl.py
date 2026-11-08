"""T120：mcp-gateway 工具级 role ACL（audit-findings-remediation-r3 批次 8）。

覆盖（spec specs/security/authn「工具级角色授权」）：
- 低权限身份调写类工具（data_platform.write_human_review_truth）被拒：
  MCPPermissionDeniedError + 目标数据零变更（工具函数未被调用）+ 审计 error=permission_denied
- 具备角色（admin / API_KEY_ROLE 缺省身份）调用放行，正常分发返回工具结果
- 白名单形态守护：TOOL_ROLE_ACL 与 TOOL_SPECS 注册表双向全覆盖，写类仅 admin
- ENABLE_AUTH=false 本地形态不做 ACL（本地放行语义留痕）
- 鉴权先行回归：未认证请求仍 401/B0002（ACL 位于 dispatch 层，先过鉴权中间件）
- 拒绝可观测：协议层（FastMCP）经 ToolError 转客户端可见错误，MUST NOT 静默放行

环境说明：本机解释器按仓库 requirements 锁定 pydantic==2.5.3，与 mcp 1.30
func_metadata 的裸 Annotated 建模不兼容（收集期 INTERNALERROR，基线既有问题）。
导入 app.mcp_server 失败时对 FastMCP.add_tool 打 stub 规避工具 schema 建模
（dispatch/ACL 路径不受影响），并将依赖真实注册表的协议层用例标记 skip。
"""
from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone as dt_tz

import httpx
import jwt as pyjwt
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from common.config.settings import settings as common_settings

from app import audit
from app.auth import (
    TOOL_ROLE_ACL,
    MCPAuthMiddleware,
    get_current_role,
    set_current_role,
)
from app.config import settings as gw_settings
from app.mcp_errors import MCPPermissionDeniedError
from app.tools import TOOL_ROUTES, TOOL_SPECS, ToolRoute, _empty_list_fallback

try:
    from app import mcp_server

    _REGISTRY_STUBBED = False
except Exception:  # noqa: BLE001 — pydantic 2.5.3 锁定基线 × mcp func_metadata 不兼容
    import mcp.server.fastmcp.server as _fsm_server

    # stub 掉工具注册：_register_tools 走空实现，dispatch/ACL 逻辑完整可用
    _fsm_server.FastMCP.add_tool = lambda self, *a, **k: None
    from app import mcp_server

    _REGISTRY_STUBBED = True


@pytest.fixture(autouse=True)
def acl_env(monkeypatch):
    """每用例复位：ACL 依赖开关（ENABLE_AUTH 缺省 false）+ 角色上下文 + 审计快照。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", False)
    set_current_role(None)
    audit.reset_call_log()
    audit.reset_fallback_error()
    yield
    set_current_role(None)
    audit.reset_call_log()
    audit.reset_fallback_error()


def _patch_route(monkeypatch, server, tool, handler=None, fallback=None):
    monkeypatch.setitem(
        TOOL_ROUTES[server],
        tool,
        ToolRoute(handler=handler, fallback=fallback or _empty_list_fallback),
    )


def _probe_app() -> Starlette:
    """挂 MCPAuthMiddleware 的最小探测应用：下游 handler 回显 dispatch 层可见角色。"""

    async def mcp_stub(request):  # noqa: ARG001
        return JSONResponse({"reached": "mcp", "role_in_dispatch": get_current_role()})

    star = Starlette(routes=[Route("/mcp", mcp_stub, methods=["POST"])])
    star.add_middleware(MCPAuthMiddleware)
    return star


def _jwt_headers(role: str) -> dict[str, str]:
    """构造有效 JWT 头（指定角色，T69 要求 type=access）。"""
    token = pyjwt.encode(
        {
            "sub": "user-t120",
            "role": role,
            "type": "access",
            "exp": datetime.now(dt_tz.utc).timestamp() + 3600,
        },
        common_settings.JWT_SECRET,
        algorithm=common_settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def _auth_headers(monkeypatch) -> dict[str, str]:  # noqa: ARG001 — 保持既有调用签名
    """构造有效 admin JWT 头。"""
    return _jwt_headers("admin")


def _rpc_result(resp: httpx.Response) -> dict:
    """解析 streamable-http 响应（application/json 或 SSE data 行）。"""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:"):].strip())
                if "result" in payload or "error" in payload:
                    return payload
        raise AssertionError(f"SSE 响应中未找到 JSON-RPC 结果: {resp.text[:200]}")
    return resp.json()


# ---------- 低权限拒绝 / 具备角色放行 ----------


async def test_low_role_write_tool_denied_no_data_change(isolate, monkeypatch):
    """低权限身份调 write_human_review_truth：拒绝、零数据变更、审计 permission_denied。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    calls = {"n": 0}

    async def write_handler(params, *, trace_id=""):  # noqa: ARG001
        calls["n"] += 1
        return {"written": True}

    _patch_route(monkeypatch, "data_platform", "write_human_review_truth", handler=write_handler)
    set_current_role("operator")  # 低权限已认证身份
    with pytest.raises(MCPPermissionDeniedError) as excinfo:
        await mcp_server.dispatch(
            "data_platform", "write_human_review_truth",
            {"session_id": "sess-1"}, trace_id="trace-t120-deny",
        )
    # 目标数据零变更：工具函数（下游回写）未被调用
    assert calls["n"] == 0
    # 审计走既有 except 通道：status=error + error=permission_denied（可观测）
    entry = audit.get_call_log()[-1]
    assert entry["status"] == "error"
    assert entry["error"] == "permission_denied"
    assert entry["trace_id"] == "trace-t120-deny"
    # 拒绝 message 不泄露内部细节（调用方角色 / 白名单词表）
    msg = str(excinfo.value)
    assert "operator" not in msg
    assert "admin" not in msg


async def test_admin_write_tool_allowed_returns_result(isolate, monkeypatch):
    """具备角色身份（admin）调写类工具：正常分发并返回工具结果。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    calls = {"n": 0}

    async def write_handler(params, *, trace_id=""):  # noqa: ARG001
        calls["n"] += 1
        return {"written": True, "session_id": params["session_id"]}

    _patch_route(monkeypatch, "data_platform", "write_human_review_truth", handler=write_handler)
    set_current_role("admin")
    result = await mcp_server.dispatch(
        "data_platform", "write_human_review_truth",
        {"session_id": "sess-2"}, trace_id="trace-t120-allow",
    )
    assert calls["n"] == 1
    assert result == {"written": True, "session_id": "sess-2"}
    assert audit.get_call_log()[-1]["status"] == "success"


async def test_write_tool_denied_when_role_missing_failsafe(isolate, monkeypatch):
    """ENABLE_AUTH=true 但角色上下文缺失（JWT 无 role / contextvar 不可达）：
    写类 fail-safe 拒绝，MUST NOT 降级为匿名执行。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    calls = {"n": 0}

    async def write_handler(params, *, trace_id=""):  # noqa: ARG001
        calls["n"] += 1
        return {"written": True}

    _patch_route(monkeypatch, "data_platform", "write_human_review_truth", handler=write_handler)
    assert get_current_role() is None  # 身份上下文缺失
    with pytest.raises(MCPPermissionDeniedError):
        await mcp_server.dispatch("data_platform", "write_human_review_truth", {"session_id": "s"})
    assert calls["n"] == 0


async def test_read_tools_allowed_for_non_admin_role(isolate, monkeypatch):
    """读类工具（白名单空集）对任意已认证角色放行：operator 抽样两个 server。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    set_current_role("operator")

    async def ok(params, *, trace_id=""):  # noqa: ARG001
        return [{"hit": 1}]

    _patch_route(monkeypatch, "rag_kb", "search_regulations", handler=ok)
    _patch_route(monkeypatch, "business_ticket", "query_case_info", handler=ok)
    r1 = await mcp_server.dispatch("rag_kb", "search_regulations", {"query": "q"})
    r2 = await mcp_server.dispatch("business_ticket", "query_case_info", {"case_id": "c1"})
    assert r1 == [{"hit": 1}]
    assert r2 == [{"hit": 1}]


async def test_api_key_default_admin_identity_allows_all_tools(isolate, monkeypatch):
    """白名单 MUST 覆盖现状默认身份：API Key 身份 role=API_KEY_ROLE（缺省 admin）
    时全部 10 个已注册工具放行（不破坏既有部署）。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    assert common_settings.API_KEY_ROLE == "admin"  # 缺省词表（现场事实）
    set_current_role(common_settings.API_KEY_ROLE)

    async def ok(params, *, trace_id=""):  # noqa: ARG001
        return []

    for spec in TOOL_SPECS:
        server, name = spec["server"], spec["name"]
        _patch_route(monkeypatch, server, name, handler=ok)
        result = await mcp_server.dispatch(server, name, {})
        assert result == [], f"{server}.{name} 在默认 admin 身份下应放行"


# ---------- 白名单形态守护 ----------


def test_whitelist_covers_all_registered_tools():
    """形态守护：TOOL_ROLE_ACL 与 TOOL_SPECS 注册表双向全覆盖；写类映射仅 admin。"""
    registered = {f"{spec['server']}.{spec['name']}" for spec in TOOL_SPECS}
    assert registered == set(TOOL_ROLE_ACL), (
        f"白名单未覆盖的工具: {registered - set(TOOL_ROLE_ACL)}; "
        f"白名单多余的工具: {set(TOOL_ROLE_ACL) - registered}"
    )
    # 写类（data_platform 两个 PUT 回写工具）仅 admin；读类为空集（任意已认证角色）
    for name in ("data_platform.write_session", "data_platform.write_human_review_truth"):
        assert TOOL_ROLE_ACL[name] == frozenset({"admin"})
    read_tools = registered - {"data_platform.write_session", "data_platform.write_human_review_truth"}
    for name in read_tools:
        assert TOOL_ROLE_ACL[name] == frozenset(), f"读类工具白名单应为空集: {name}"


# ---------- ENABLE_AUTH=false 本地形态留痕 ----------


async def test_auth_disabled_local_passthrough_allows_write(isolate, monkeypatch):
    """ENABLE_AUTH=false（本地形态）无身份上下文：不做 ACL，写类同样放行
    （与鉴权中间件「默认关闭直通」的本地放行语义一致）。"""
    assert gw_settings.ENABLE_AUTH is False
    calls = {"n": 0}

    async def write_handler(params, *, trace_id=""):  # noqa: ARG001
        calls["n"] += 1
        return {"written": True}

    _patch_route(monkeypatch, "data_platform", "write_human_review_truth", handler=write_handler)
    result = await mcp_server.dispatch("data_platform", "write_human_review_truth", {"session_id": "s"})
    assert calls["n"] == 1
    assert result == {"written": True}


# ---------- 中间件 → dispatch 的角色上下文传递 + 鉴权先行回归 ----------


async def test_auth_middleware_propagates_role_contextvar(isolate, monkeypatch):
    """鉴权成功的角色经 contextvar 传递到 dispatch 层：JWT（role=admin）与
    API Key（role=API_KEY_ROLE 缺省）两种身份形态均生效；拒绝路径不污染上下文。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(gw_settings, "AUTH_API_KEY", "ak-t120-test")
    monkeypatch.setattr(common_settings, "API_KEYS", "")
    transport = httpx.ASGITransport(app=_probe_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        # JWT 身份：payload.role 原样透传
        r_jwt = await client.post("/mcp", json={}, headers=_auth_headers(monkeypatch))
        assert r_jwt.status_code == 200
        assert r_jwt.json()["role_in_dispatch"] == "admin"
        # API Key 身份：role 取 settings.API_KEY_ROLE（缺省 admin，现状默认身份）
        r_key = await client.post(
            "/mcp", json={}, headers={"Authorization": "Bearer ak-t120-test"}
        )
        assert r_key.status_code == 200
        assert r_key.json()["role_in_dispatch"] == common_settings.API_KEY_ROLE == "admin"
        # 拒绝路径不设置角色上下文（401 响应内 role 仍为 None）
        r_deny = await client.post("/mcp", json={})
        assert r_deny.status_code == 401
    assert get_current_role() is None  # 放行请求结束后上下文已 reset，不跨请求残留


async def test_unauthenticated_still_401_before_acl(isolate, monkeypatch):
    """鉴权先行回归：ENABLE_AUTH=true 无凭证请求仍 401/B0002（JSON-RPC 信封），
    ACL 位于 dispatch 层，不影响鉴权中间件的拒绝行为。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    rpc = {
        "jsonrpc": "2.0", "id": 120,
        "method": "tools/call",
        "params": {"name": "data_platform.write_human_review_truth", "arguments": {"session_id": "s"}},
    }
    transport = httpx.ASGITransport(app=_probe_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/mcp", json=rpc)
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["data"]["code"] == "B0002"
    assert body["id"] == 120


# ---------- 角色词表大小写 + 协议层可观测 ----------


async def test_role_matching_case_insensitive(isolate, monkeypatch):
    """角色比较大小写不敏感：JWT 大写词形态（role=ADMIN，见 test_t110 用例）同 admin。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)

    async def write_handler(params, *, trace_id=""):  # noqa: ARG001
        return {"written": True}

    _patch_route(monkeypatch, "data_platform", "write_human_review_truth", handler=write_handler)
    set_current_role("ADMIN")
    result = await mcp_server.dispatch(
        "data_platform", "write_human_review_truth", {"session_id": "s"}
    )
    assert result == {"written": True}


@pytest.mark.skipif(
    _REGISTRY_STUBBED,
    reason="pydantic 2.5.3 锁定基线下 FastMCP 注册表为 stub，协议层用例在兼容环境执行",
)
async def test_permission_denied_client_visible_via_protocol(isolate, monkeypatch):
    """协议层可观测：低权限调写类工具经 FastMCP 转 ToolError（客户端可见，
    message 携带 ACL 拒绝语义），MUST NOT 静默放行返回业务结果。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    from mcp.server.fastmcp.exceptions import ToolError

    async def write_handler(params, *, trace_id=""):  # noqa: ARG001
        return {"written": True}

    _patch_route(monkeypatch, "data_platform", "write_human_review_truth", handler=write_handler)
    set_current_role("operator")
    with pytest.raises(ToolError, match="insufficient role"):
        await mcp_server.mcp.call_tool(
            "data_platform.write_human_review_truth", {"session_id": "s"}
        )


@pytest.mark.skipif(
    _REGISTRY_STUBBED,
    reason="pydantic 2.5.3 锁定基线下 FastMCP 注册表为 stub，链路用例在兼容环境执行",
)
async def test_end_to_end_middleware_to_fastmcp_acl(isolate, monkeypatch):
    """端到端串联：MCPAuthMiddleware → streamable-http（stateless） →
    FastMCP tools/call → 真实 dispatch ACL。角色上下文经真实请求 header 传递
    （非手工 set），低权限 operator 被拒且工具函数零调用，admin 放行。

    使用独立的 FastMCP 实例而非 app.main：StreamableHTTPSessionManager.run()
    每实例仅允许一次，进程内已有真实 app 集成用例（test_batch4）占用该实例。"""
    monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(gw_settings, "AUTH_API_KEY", "ak-t120-e2e")
    monkeypatch.setattr(common_settings, "API_KEYS", "")

    calls = {"n": 0}

    async def write_handler(params, *, trace_id=""):  # noqa: ARG001
        calls["n"] += 1
        return {"written": True, "session_id": params["session_id"]}

    _patch_route(monkeypatch, "data_platform", "write_human_review_truth", handler=write_handler)

    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    fresh = FastMCP(
        name="mcp-gateway-t120-e2e",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["mcp-gateway:*", "localhost", "127.0.0.1", "::1"],
        ),
    )

    async def write_tool(session_id: str) -> dict:
        return await mcp_server.dispatch(
            "data_platform", "write_human_review_truth", {"session_id": session_id}
        )

    fresh.add_tool(write_tool, name="data_platform.write_human_review_truth", description="T120 e2e")

    outer = Starlette()
    outer.add_middleware(MCPAuthMiddleware)
    outer.mount("/", fresh.streamable_http_app())

    rpc = {
        "jsonrpc": "2.0",
        "id": 120,
        "method": "tools/call",
        "params": {
            "name": "data_platform.write_human_review_truth",
            "arguments": {"session_id": "sess-e2e"},
        },
    }
    base_headers = {"Accept": "application/json, text/event-stream"}

    async with fresh.session_manager.run():
        transport = httpx.ASGITransport(app=outer)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mcp-gateway:9000"
        ) as client:
            denied = await client.post(
                "/mcp", json=rpc, headers={**base_headers, **_jwt_headers("operator")}
            )
            assert denied.status_code == 200
            deny_result = _rpc_result(denied).get("result") or {}
            deny_text = " ".join(
                str(item.get("text", "")) for item in deny_result.get("content", [])
            )
            assert deny_result.get("isError") is True
            assert "insufficient role" in deny_text
            assert "written" not in deny_text
            assert calls["n"] == 0

            allowed = await client.post(
                "/mcp",
                json={**rpc, "id": 121},
                headers={**base_headers, **_jwt_headers("admin")},
            )
            assert allowed.status_code == 200
            allow_result = _rpc_result(allowed).get("result") or {}
            allow_text = " ".join(
                str(item.get("text", "")) for item in allow_result.get("content", [])
            )
            assert allow_result.get("isError") is not True
            assert "written" in allow_text
            assert calls["n"] == 1