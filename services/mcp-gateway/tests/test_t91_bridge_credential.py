"""T91 返工：mcp-gateway 桥出站服务间凭证注入。

- 动态签发形态：Authorization 为共享密钥签发的 service:mcp-gateway JWT
  （dp 侧 require_service_credential 验签 + role=="service" 接受）
- 静态配置形态：DP_SERVICE_TOKEN 优先，原值透传
- X-Trace-Id 既有语义保持
"""
from __future__ import annotations

import httpx
import pytest

from app.bridge_http import BridgeHttpClient
from app.config import settings
from common.security.rbac import verify_token


def _client(seen: dict[str, str]) -> BridgeHttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization", "")
        seen["trace_id"] = request.headers.get("X-Trace-Id", "")
        return httpx.Response(200, json={"code": "0", "data": {"ok": True}})

    return BridgeHttpClient(
        "http://data-platform:8000", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_outbound_carries_valid_service_jwt():
    seen: dict[str, str] = {}
    client = _client(seen)
    await client.request("GET", "/api/v1/data/snapshot/case-1", trace_id="t-91")

    auth = seen["authorization"]
    assert auth.startswith("Bearer ")
    payload = verify_token(auth.removeprefix("Bearer "))
    assert payload is not None
    assert payload["sub"] == "service:mcp-gateway"
    assert payload["role"] == "service"
    assert seen["trace_id"] == "t-91"
    await client.aclose()


@pytest.mark.asyncio
async def test_static_token_preferred_when_configured(monkeypatch):
    seen: dict[str, str] = {}
    monkeypatch.setattr(settings, "DP_SERVICE_TOKEN", "static-service-token")
    client = _client(seen)
    await client.request("PUT", "/api/v1/data/snapshot/case-1", json_body={"a": 1})
    assert seen["authorization"] == "Bearer static-service-token"
    await client.aclose()
