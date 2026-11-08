"""T91：qc DpClient 出站服务间凭证注入——出站头注入后 dp 验签通过。

- 动态签发形态：Authorization 为共享密钥签发的 service:qc-service JWT
- 静态配置形态：QC_DP_SERVICE_TOKEN 优先
- pending-qc 同样注入
"""
from __future__ import annotations

import httpx
import pytest

from app.core.config import qc_settings
from app.integrations.dp_client import DpClient
from common.security.rbac import verify_token
from tests.test_dp_client import _envelope, _fixture  # noqa: F401  复用既有夹具


@pytest.mark.asyncio
async def test_outbound_carries_valid_service_jwt(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json=_envelope(_fixture()))

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
    await client.get_qc_source("sess-fixture-001")

    auth = seen["authorization"]
    assert auth.startswith("Bearer ")
    payload = verify_token(auth.removeprefix("Bearer "))
    assert payload is not None
    assert payload["sub"] == "service:qc-service"
    assert payload["role"] == "service"


@pytest.mark.asyncio
async def test_static_token_preferred_when_configured(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json=_envelope(_fixture()))

    monkeypatch.setattr(qc_settings, "QC_DP_SERVICE_TOKEN", "static-service-token")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
    await client.get_qc_source("sess-fixture-001")
    assert seen["authorization"] == "Bearer static-service-token"


@pytest.mark.asyncio
async def test_pending_qc_also_carries_credential(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={"code": "0", "data": {"session_ids": ["s1"]}, "trace_id": "t"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
    ids = await client.list_pending_session_ids(etl_date="2026-11-07")
    assert ids == ["s1"]
    assert seen and seen[0].startswith("Bearer ")