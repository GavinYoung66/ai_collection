"""T91 返工：complaint 直调 dp snapshot 的出站服务间凭证断言。

_fetch_snapshot_ctx 以共享 JWT_SECRET 动态签发内部服务 JWT
（sub=service:complaint-service、role=service），dp 侧
require_service_credential 验签 + role=="service" 接受。
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.service.complaint_text_builder import _fetch_snapshot_ctx
from common.security.rbac import verify_token


@pytest.mark.asyncio
async def test_snapshot_outbound_carries_service_jwt(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {"session_id": "sess-1", "customer_name": "张三", "overdue_amount": 100},
            },
        )

    real_cls = httpx.AsyncClient

    def factory(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs.pop("transport", None)
        return real_cls(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout", 5.0))

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr(settings, "DATA_PLATFORM_URL", "http://data-platform:8000")

    sid, customer, overdue = await _fetch_snapshot_ctx("case-1")
    assert (sid, customer, overdue) == ("sess-1", "张三", "100")

    auth = seen["authorization"]
    assert auth.startswith("Bearer ")
    payload = verify_token(auth.removeprefix("Bearer "))
    assert payload is not None
    assert payload["sub"] == "service:complaint-service"
    assert payload["role"] == "service"
