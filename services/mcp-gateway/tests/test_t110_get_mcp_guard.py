"""T110：mcp-gateway GET /mcp 方法覆盖（扩展形态——取证结论 B 实证 GET 建立匿名 SSE 流）。

- 未认证 GET /mcp → 401（JSON-RPC 信封形态，与 POST 拒绝同风格），无匿名 SSE 放行路径
- 已认证 GET /mcp → 放行进入下游（SSE 协议行为不变）
- ENABLE_AUTH=false 默认直通语义不变
- GET /metrics、GET /health 豁免不误伤
- _protected_kind 矩阵守护：GET/POST /mcp → "mcp"；GET /metrics、/health → None
"""
from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.auth import MCPAuthMiddleware, _protected_kind
from app.config import settings as gw_settings


def _guard_app() -> Starlette:
    """挂 MCPAuthMiddleware 的最小探测应用（/mcp GET+POST + /health + /metrics）。"""

    async def mcp_get_stub(request):  # noqa: ARG001
        async def gen():
            yield 'data: {"sse": true}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def mcp_post_stub(request):  # noqa: ARG001
        return JSONResponse({"reached": "mcp", "echo": {"served": True}})

    async def health_stub(request):  # noqa: ARG001
        return JSONResponse({"ok": True})

    async def metrics_stub(request):  # noqa: ARG001
        return JSONResponse({"metrics": 1})

    star = Starlette(
        routes=[
            Route("/mcp", mcp_get_stub, methods=["GET"]),
            Route("/mcp", mcp_post_stub, methods=["POST"]),
            Route("/health", health_stub, methods=["GET"]),
            Route("/metrics", metrics_stub, methods=["GET"]),
        ]
    )
    star.add_middleware(MCPAuthMiddleware)
    return star


@pytest.fixture()
def transport():
    return httpx.ASGITransport(app=_guard_app())


class TestProtectedKindMatrix:
    @pytest.mark.asyncio
    async def test_get_mcp_is_protected(self):
        scope = {"type": "http", "method": "GET", "path": "/mcp", "headers": []}
        assert _protected_kind(scope) == "mcp"

    @pytest.mark.asyncio
    async def test_post_mcp_still_protected(self):
        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
        assert _protected_kind(scope) == "mcp"

    @pytest.mark.asyncio
    async def test_metrics_and_health_exempt(self):
        assert _protected_kind({"type": "http", "method": "GET", "path": "/metrics", "headers": []}) is None
        assert _protected_kind({"type": "http", "method": "GET", "path": "/health", "headers": []}) is None


class TestGetMcpGuard:
    @pytest.mark.asyncio
    async def test_unauthenticated_get_mcp_401_jsonrpc_envelope(self, transport, monkeypatch):
        monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp-gateway:9000") as client:
            resp = await client.get("/mcp", headers={"Accept": "text/event-stream"})
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["data"]["code"] == "B0002"
        assert "text/event-stream" not in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_authenticated_get_mcp_passes_through(self, transport, monkeypatch):
        monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
        from common.security.rbac import create_token

        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {create_token({'sub': 'u-admin', 'role': 'ADMIN'})}",
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp-gateway:9000") as client:
            resp = await client.get("/mcp", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    @pytest.mark.asyncio
    async def test_auth_disabled_keeps_passthrough(self, transport, monkeypatch):
        monkeypatch.setattr(gw_settings, "ENABLE_AUTH", False)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp-gateway:9000") as client:
            resp = await client.get("/mcp", headers={"Accept": "text/event-stream"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_metrics_and_health_not_broken(self, transport, monkeypatch):
        monkeypatch.setattr(gw_settings, "ENABLE_AUTH", True)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp-gateway:9000") as client:
            metrics = await client.get("/metrics")
            health = await client.get("/health")
        assert metrics.status_code == 200
        assert health.status_code == 200