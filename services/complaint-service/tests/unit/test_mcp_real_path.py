"""MCP 真路径单测（Batch 5：Agent MCP SDK client → 独立 mcp-gateway）。

- validate_real_mcp_config 校验 MCP_GATEWAY_URL（真路径唯一依赖）
- RealMCPTransport 经 MCP 协议（streamable-http）调桩网关：4 server 路由、
  dict/list 形状还原、_meta 透传、isError、401 权限拒绝、薄超时、未知 server
"""
from __future__ import annotations

import asyncio
import socket
import threading
from contextlib import asynccontextmanager

import pytest
import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.config import settings
from app.mcp.client import MCPClient
from app.mcp.errors import MCPError, MCPPermissionDeniedError, MCPTimeoutError
from app.mcp.gateway import MCPGateway
from app.mcp.real_transport import RealMCPTransport, _LIST_SHAPE_TOOLS
from app.mcp.validate import MCPConfigError, validate_real_mcp_config


def test_validate_real_mcp_skips_when_mock(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
    monkeypatch.setattr(settings, "MCP_GATEWAY_URL", "")
    validate_real_mcp_config()


def test_validate_real_mcp_fails_without_gateway_url(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "MCP_GATEWAY_URL", "change_me")
    with pytest.raises(MCPConfigError, match="MCP_GATEWAY_URL"):
        validate_real_mcp_config()


def test_validate_real_mcp_fails_when_empty(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "MCP_GATEWAY_URL", "")
    with pytest.raises(MCPConfigError, match="MCP_GATEWAY_URL"):
        validate_real_mcp_config()


def test_validate_real_mcp_ok_with_url(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "MCP_GATEWAY_URL", "http://mcp-gateway:9000")
    validate_real_mcp_config()


def test_list_shape_tools_contract():
    """list 形状工具静态集合与网关 10 工具契约对齐。

    mcp 1.30 FastMCP 对 list 返回值按「每元素一个 TextContent」编码，客户端无法
    区分 list/dict；本集合必须与网关 TOOL_SPECS 中「返回 list 的工具」一致。
    网关新增 list 工具时须同步更新这里，否则被误解析为 dict/空。
    """
    expected = {
        ("rag_kb", "search_regulations"),
        ("rag_kb", "search_response_templates"),
        ("business_ticket", "query_history_tickets"),
        ("business_ticket", "query_history_complaints"),
        ("call_qc", "query_call_asr"),
        ("call_qc", "query_qc_violations"),
        ("call_qc", "query_qc_alerts"),
    }
    assert set(_LIST_SHAPE_TOOLS) == expected


# ── Batch 5：RealMCPTransport 经 MCP SDK client 连独立 mcp-gateway ──


def _meta(ctx: Context, key: str) -> str:
    try:
        meta = ctx.request_context.meta if ctx and ctx.request_context else None
        return getattr(meta, key, "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _build_stub_gateway_app():
    """与真网关同构的桩：FastAPI /health + Mount FastMCP(streamable /mcp)。

    工具名保持网关静态注册命名 ``{server}.{tool}``，覆盖 dict/list/异常/慢响应。
    """
    from fastapi import FastAPI

    mcp = FastMCP(name="stub-mcp-gateway", stateless_http=True)

    async def rag_search(query: str, top_k: int = 5, ctx: Context = None) -> list:
        return [{"doc_id": "REG-1", "content_snippet": query[:10], "trace": _meta(ctx, "trace_id")}]

    async def case_info(case_id: str, ctx: Context = None) -> dict:
        return {
            "case_id": case_id,
            "customer_name": "张三",
            "phone": "13800138000",  # 桩网关故意不脱敏，检验 Agent 第二道
            "trace": _meta(ctx, "trace_id"),
            "sess": _meta(ctx, "session_id"),
        }

    async def call_asr(case_id: str, ctx: Context = None) -> list:
        return [
            {"turn_id": 1, "speaker": "agent", "content": "你好"},
            {"turn_id": 2, "speaker": "user", "content": "投诉"},
        ]

    async def write_session(
        session_id: str, task_status: str = "", verdict: str = "",
        case_id: str = "", complaint_id: str = "", ctx: Context = None,
    ) -> dict:
        return {"success": True, "persisted": True, "session_id": session_id,
                "task_status": task_status}

    async def boom(ctx: Context = None) -> dict:
        raise RuntimeError("downstream boom")

    async def slow(ctx: Context = None) -> dict:
        await asyncio.sleep(1.0)
        return {"ok": True}

    for fn, name in (
        (rag_search, "rag_kb.search_regulations"),
        (case_info, "business_ticket.query_case_info"),
        (call_asr, "call_qc.query_call_asr"),
        (write_session, "data_platform.write_session"),
        (boom, "rag_kb.boom_tool"),
        (slow, "rag_kb.slow_tool"),
    ):
        mcp.add_tool(fn, name=name)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 与 services/mcp-gateway/app/main.py 一致：显式运行 MCP session manager
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"code": "0", "message": "ok", "data": {"status": "healthy"}, "trace_id": "stub"}

    app.mount("/", mcp.streamable_http_app())
    return app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 5.0) -> None:
    import time as _time

    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            _time.sleep(0.05)
    raise RuntimeError(f"stub gateway on port {port} not ready")


@pytest.fixture(scope="module")
def stub_gateway_base():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(_build_stub_gateway_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_port(port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _build_reject_app(status_code: int, code: str):
    async def reject(request):
        return JSONResponse(
            status_code=status_code,
            content={"jsonrpc": "2.0",
                     "error": {"code": -32001, "message": "unauthorized", "data": {"code": code}},
                     "id": None},
        )

    return Starlette(routes=[Route("/mcp", reject, methods=["POST"])])


@pytest.mark.asyncio
async def test_real_transport_routes_via_mcp_sdk(stub_gateway_base):
    """4 server 经 MCP 协议调桩网关：形状对齐 fixture + trace/session meta 透传 + PII 第二道。"""
    client = MCPClient(transport=RealMCPTransport(gateway_url=stub_gateway_base))

    # rag_kb：单元素 list（单 content block）仍还原为 list
    hits = await client.call(
        "rag_kb", "search_regulations", {"query": "夜间催收"}, trace_id="T-1", session_id="S-1"
    )
    assert isinstance(hits, list) and hits[0]["doc_id"] == "REG-1"
    assert hits[0]["trace"] == "T-1"  # _meta 透传到网关

    # business_ticket：dict 形状 + meta 透传 + Agent 侧 PII 二次脱敏
    case = await client.call(
        "business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="T-1", session_id="S-1"
    )
    assert isinstance(case, dict) and case["case_id"] == "c1" and case["sess"] == "S-1"
    assert "13800138000" not in str(case)

    # call_qc：多元素 list（多 content block 聚合）
    asr = await client.call("call_qc", "query_call_asr", {"case_id": "c1"})
    assert isinstance(asr, list) and len(asr) == 2 and asr[1]["speaker"] == "user"

    # data_platform：dict 回写形状
    out = await client.call(
        "data_platform", "write_session",
        {"session_id": "s1", "task_status": "resolved", "verdict": "violation"},
    )
    assert out["success"] is True and out["task_status"] == "resolved"


@pytest.mark.asyncio
async def test_real_transport_tool_iserror_raises_mcp_error(stub_gateway_base):
    client = MCPClient(transport=RealMCPTransport(gateway_url=stub_gateway_base))
    with pytest.raises(MCPError, match="gateway tool error"):
        await client.call("rag_kb", "boom_tool", {})


@pytest.mark.asyncio
async def test_real_transport_thin_timeout(stub_gateway_base):
    """Agent 侧薄超时（MCPClient.wait_for）先于网关慢响应触发 → MCPTimeoutError。

    （Batch 5 审核 P2-1）此前 integration/e2e conftest 全局 nest_asyncio.apply()
    污染事件循环导致本用例被 skip；已移除该全局补丁，真实链路薄超时断言恢复常态运行。
    """
    client = MCPClient(transport=RealMCPTransport(gateway_url=stub_gateway_base), timeout=0.2)
    with pytest.raises(MCPTimeoutError):
        await client.call("rag_kb", "slow_tool", {})


@pytest.mark.asyncio
async def test_real_transport_unknown_server_rejects_without_network():
    t = RealMCPTransport(gateway_url="http://127.0.0.1:9")  # 即使给了 URL 也不建连
    with pytest.raises(MCPError, match="unknown MCP server"):
        await t.call("case_server", "query_case_info", {})


@pytest.mark.asyncio
async def test_real_transport_missing_gateway_url_raises():
    t = RealMCPTransport(gateway_url="")
    with pytest.raises(MCPError, match="MCP_GATEWAY_URL"):
        await t.call("rag_kb", "search_regulations", {"query": "q"})


@pytest.mark.asyncio
async def test_real_transport_auth_401_maps_permission_denied():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(_build_reject_app(401, "B0002"), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_port(port)
    try:
        client = MCPClient(transport=RealMCPTransport(gateway_url=f"http://127.0.0.1:{port}"))
        with pytest.raises(MCPPermissionDeniedError, match="401"):
            await client.call("rag_kb", "search_regulations", {"query": "q"})
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_real_transport_does_not_reference_inprocess_bridges():
    """进程内桥 SHALL NOT 被真路径 transport 引用（源码级守卫）。"""
    import inspect

    source = inspect.getsource(RealMCPTransport)
    for token in (
        "RagKbBridge", "BusinessTicketBridge", "CallQcBridge", "DataPlatformBridge",
        "/mcp/call", "_forward_gateway",
    ):
        assert token not in source, f"RealMCPTransport 仍引用已迁移构件: {token}"


def test_gateway_health_check_probes_gateway(monkeypatch, stub_gateway_base):
    """真路径同步 health_check：HTTP GET 网关 /health，4 server 状态一致。"""
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    gw = MCPGateway(gateway_url=stub_gateway_base)
    health = gw.health_check()
    assert set(health) == {"rag_kb", "business_ticket", "data_platform", "call_qc"}
    assert set(health.values()) == {"healthy"}

    bad = MCPGateway(gateway_url="http://127.0.0.1:1")
    bad_health = bad.health_check()
    assert set(bad_health.values()) == {"unavailable"}


@pytest.mark.asyncio
async def test_gateway_ahealth_check_mcp_probe(monkeypatch, stub_gateway_base):
    """真路径异步 health_check：MCP 级 initialize + list_tools 探活。"""
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    gw = MCPGateway(gateway_url=stub_gateway_base)
    health = await gw.ahealth_check()
    assert set(health.values()) == {"healthy"}
