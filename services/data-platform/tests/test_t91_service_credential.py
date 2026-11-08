"""T91：data-platform 数据面服务间凭证。

- 无凭证 GET qc-source / pending-qc / snapshot → 401 B0002，响应不含任何 PII
- 无效/过期凭证 → 401；有效凭证正常拉数（响应结构不变）
- 拒绝发生在任何数据读取之前（FakeSession 零查询）
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from common.db.session import get_session
from common.security.rbac import create_token

_QC_SOURCE_PATH = "/api/v1/data/sessions/sess-t91/qc-source"
_PENDING_PATH = "/api/v1/data/sessions:pending-qc"
_SNAPSHOT_PATH = "/api/v1/data/snapshot/case-t91"

_PII_MARKERS = ("对话全文", "张三", "逾期金额", "overdue_amount", "session_ids")


class FakeSession:
    """拒绝发生在数据读取之前——若被触达即失败。"""

    async def execute(self, stmt):  # noqa: ANN001, ANN201
        raise AssertionError("DB must not be touched before credential check")

    async def commit(self):  # noqa: ANN201
        return None


async def _fake_get_qc_source(session, session_id):  # noqa: ANN001, ARG001
    return {
        "session_id": "sess-t91",
        "case_id": "case-t91",
        "call_ended": True,
        "text_ready": True,
        "debtor_name": "张三",
        "overdue_amount": 3800.0,
        "turns": [{"turn_id": "t1", "speaker": "agent", "text": "您好", "turn_index": 1}],
        "result": None,
    }


async def _fake_pending(session, *, etl_date, limit):  # noqa: ANN001, ARG001
    return ["sess-a", "sess-b"]


@pytest.fixture()
def client(monkeypatch):
    async def _session():
        yield FakeSession()

    app.dependency_overrides[get_session] = _session
    monkeypatch.setattr("app.api.qc_source.get_qc_source", _fake_get_qc_source)
    monkeypatch.setattr("app.api.qc_source.list_pending_session_ids", _fake_pending)
    return TestClient(app)


def _expired_token() -> str:

    from common.security.rbac import create_token

    return create_token({"sub": "service:qc-service", "role": "service"}, expires_hours=-1)


class TestServiceCredential:
    def test_no_credential_qc_source_401_no_pii(self, client):
        resp = client.get(_QC_SOURCE_PATH)
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["trace_id"]
        for marker in _PII_MARKERS:
            assert marker not in resp.text

    def test_no_credential_pending_qc_401_no_session_ids(self, client):
        resp = client.get(_PENDING_PATH)
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"
        assert "session_ids" not in resp.text
        assert "sess-a" not in resp.text

    def test_no_credential_snapshot_401(self, client):
        resp = client.get(_SNAPSHOT_PATH)
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"

    def test_invalid_token_401(self, client):
        resp = client.get(_QC_SOURCE_PATH, headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"

    def test_expired_token_401(self, client):
        resp = client.get(_QC_SOURCE_PATH, headers={"Authorization": f"Bearer {_expired_token()}"})
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"

    def test_valid_service_token_allows_qc_source(self, client):
        token = create_token({"sub": f"service:qc-{uuid4().hex[:6]}", "role": "service"})
        resp = client.get(_QC_SOURCE_PATH, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["session_id"] == "sess-t91"
        assert body["data"]["debtor_name"] == "张三"

    def test_valid_service_token_allows_pending_qc(self, client):
        token = create_token({"sub": "service:qc-service", "role": "service"})
        resp = client.get(_PENDING_PATH, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["data"]["session_ids"] == ["sess-a", "sess-b"]

    def test_health_exempt_from_credential(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_wrong_key_token_rejected(self, client, monkeypatch):
        from common.config.settings import settings

        original = settings.JWT_SECRET
        monkeypatch.setattr(settings, "JWT_SECRET", "other-secret")
        foreign = create_token({"sub": "service:qc-service", "role": "service"})
        monkeypatch.setattr(settings, "JWT_SECRET", original)
        resp = client.get(_QC_SOURCE_PATH, headers={"Authorization": f"Bearer {foreign}"})
        assert resp.status_code == 401

    def test_user_role_token_rejected(self, client):
        """T91 返工：有效用户角色 token（admin 等）MUST 被拒——数据面仅服务凭证。"""
        for role in ("admin", "supervisor", "agent"):
            token = create_token({"sub": str(uuid4()), "role": role})
            resp = client.get(_QC_SOURCE_PATH, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 401, f"role={role}: {resp.text}"
            assert resp.json()["code"] == "B0002"
            for marker in _PII_MARKERS:
                assert marker not in resp.text

    def test_static_preshared_token_allows(self, client, monkeypatch):
        """T91 返工：DP_SERVICE_TOKEN 配置后静态值恒定时间比对放行（非 JWT 形态）。"""
        from app.core.config import dp_settings

        monkeypatch.setattr(dp_settings, "DP_SERVICE_TOKEN", "static-service-token")
        resp = client.get(_QC_SOURCE_PATH, headers={"Authorization": "Bearer static-service-token"})
        assert resp.status_code == 200
        assert resp.json()["data"]["session_id"] == "sess-t91"
        # 不命中的静态值仍走 JWT 分支 → 401
        resp = client.get(_QC_SOURCE_PATH, headers={"Authorization": "Bearer wrong-static"})
        assert resp.status_code == 401