"""T90：complaint stats/consistency 两端点鉴权收口。

- 匿名 GET /api/v1/complaints/stats 与 /consistency → 401 B0002，不返回运营指标
- 已认证 admin/supervisor 正常返回；agent → 403
- auth_required=false 本地放行语义保持
- 挂载顺序回归：stats → mode → complaints（固定路径先于 /{id}）
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.stats import router as stats_router
from app.main import app
from common.config.settings import settings as platform_settings
from common.security.rbac import create_token

_STATS_PATH = "/api/v1/complaints/stats"
_CONSISTENCY_PATH = "/api/v1/complaints/consistency"


@pytest.fixture()
def client(monkeypatch, enable_auth: bool = True):
    monkeypatch.setattr(platform_settings, "ENABLE_AUTH", enable_auth)
    return TestClient(app)


class TestStatsAuth:
    def test_anonymous_rejected_401_no_metrics(self, client):
        for path in (_STATS_PATH, _CONSISTENCY_PATH):
            resp = client.get(path)
            assert resp.status_code == 401, path
            body = resp.json()
            assert body["code"] == "B0002"
            assert body["data"] is None

    def test_agent_role_403(self, client):
        headers = {"Authorization": f"Bearer {create_token({'sub': str(uuid4()), 'role': 'agent'})}"}
        for path in (_STATS_PATH, _CONSISTENCY_PATH):
            resp = client.get(path, headers=headers)
            assert resp.status_code == 403, path
            assert resp.json()["code"] == "B0003"

    def test_admin_and_supervisor_allowed(self, client):
        for role in ("admin", "supervisor"):
            headers = {"Authorization": f"Bearer {create_token({'sub': str(uuid4()), 'role': role})}"}
            stats = client.get(_STATS_PATH, headers=headers)
            assert stats.status_code == 200, f"{role}: {stats.text}"
            assert stats.json()["code"] == 0
            consistency = client.get(_CONSISTENCY_PATH, headers=headers)
            assert consistency.status_code == 200
            assert consistency.json()["code"] == 0

    def test_local_mode_keeps_debug_access(self, monkeypatch):
        monkeypatch.setattr(platform_settings, "ENABLE_AUTH", False)
        client = TestClient(app)
        resp = client.get(_STATS_PATH)
        assert resp.status_code == 200
        assert resp.json()["code"] == 0


class TestMountOrderRegression:
    def test_stats_before_mode_before_complaints(self):
        """stats → mode → complaints（complaints 含 /{id} 须在后，防吞路径）。"""
        routes = [r for r in app.routes if getattr(r, "path", "").startswith("/api/v1/complaints")]
        paths = [r.path for r in routes]
        stats_idx = min(i for i, p in enumerate(paths) if p.endswith("/stats"))
        mode_idx = min(i for i, p in enumerate(paths) if p.endswith("/mode"))
        complaints_idx = max(i for i, p in enumerate(paths) if p.rstrip("/") == "/api/v1/complaints")
        assert stats_router is not None
        assert mode_idx >= 0 and complaints_idx >= 0
        assert stats_idx < complaints_idx and mode_idx < complaints_idx