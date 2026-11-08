"""T122：stats/consistency 两端点标记演示数据源（audit-findings-remediation-r3 8.3）。

- stats 响应含 data_source="demo_stub"
- consistency 响应含 data_source="demo_stub"
- 字段类型/值断言；其余既有字段不回归
- 鉴权回归：匿名 401（对齐 test_t90_stats_auth 的 token 构造方式）
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from common.config.settings import settings as platform_settings

_STATS_PATH = "/api/v1/complaints/stats"
_CONSISTENCY_PATH = "/api/v1/complaints/consistency"

_EXPECTED_STATS_KEYS = {
    "total",
    "by_verdict",
    "avg_processing_minutes",
    "evidence_insufficient_rate",
    "mcp_failure_rate",
    "ai_human_consistency_rate",
}
_EXPECTED_CONSISTENCY_KEYS = {
    "ai_human_consistency_rate",
    "total_reviewed",
    "by_verdict_consistency",
}


@pytest.fixture()
def client(monkeypatch):
    """本地放行模式（ENABLE_AUTH=False），与既有 stats 路由测试同口径。"""
    monkeypatch.setattr(platform_settings, "ENABLE_AUTH", False)
    return TestClient(app)


@pytest.fixture()
def authed_client(monkeypatch):
    """开认证模式，供鉴权回归用例。"""
    monkeypatch.setattr(platform_settings, "ENABLE_AUTH", True)
    return TestClient(app)


class TestStatsDataSourceMarker:
    def test_stats_response_has_demo_stub_marker(self, client):
        resp = client.get(_STATS_PATH)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["data_source"] == "demo_stub"

    def test_consistency_response_has_demo_stub_marker(self, client):
        resp = client.get(_CONSISTENCY_PATH)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["data_source"] == "demo_stub"

    def test_marker_field_type_and_value(self, client):
        """标记字段为字符串 demo_stub，非演示形态的数值/布尔。"""
        for path in (_STATS_PATH, _CONSISTENCY_PATH):
            data = client.get(path).json()["data"]
            assert isinstance(data["data_source"], str), path
            assert data["data_source"] == "demo_stub", path
            assert data["data_source"] != "real", path

    def test_marker_field_required_by_schema(self):
        """T122 加固：data_source 为必填字段，新增响应构造点漏写会立即报错，
        不会静默沿用演示标记（路由层显式写入 STATS_DATA_SOURCE）。"""
        from app.api.schemas.complaint import ConsistencyResponse, StatsResponse

        for model in (StatsResponse, ConsistencyResponse):
            field = model.model_fields["data_source"]
            assert field.is_required(), f"{model.__name__}.data_source 必须显式赋值"

    def test_existing_stats_fields_not_regressed(self, client):
        """stats 其余既有字段不回归：键齐全且类型正确。"""
        data = client.get(_STATS_PATH).json()["data"]
        assert _EXPECTED_STATS_KEYS <= set(data)
        assert isinstance(data["total"], int)
        assert isinstance(data["by_verdict"], dict) and data["by_verdict"]
        for key in ("avg_processing_minutes", "evidence_insufficient_rate",
                    "mcp_failure_rate", "ai_human_consistency_rate"):
            assert isinstance(data[key], (int, float)), key

    def test_existing_consistency_fields_not_regressed(self, client):
        """consistency 其余既有字段不回归：键齐全且类型正确。"""
        data = client.get(_CONSISTENCY_PATH).json()["data"]
        assert _EXPECTED_CONSISTENCY_KEYS <= set(data)
        assert isinstance(data["ai_human_consistency_rate"], float)
        assert isinstance(data["total_reviewed"], int)
        assert isinstance(data["by_verdict_consistency"], dict) and data["by_verdict_consistency"]


class TestStatsAuthRegression:
    def test_anonymous_rejected_401_when_auth_enabled(self, authed_client):
        """鉴权回归：开认证后匿名 401 B0002，不外发演示指标。"""
        for path in (_STATS_PATH, _CONSISTENCY_PATH):
            resp = authed_client.get(path)
            assert resp.status_code == 401, path
            body = resp.json()
            assert body["code"] == "B0002", path
            assert body["data"] is None, path

    def test_admin_token_allowed_with_marker(self, monkeypatch):
        """admin token 正常访问，响应仍带 demo_stub 标记。"""
        from common.security.rbac import create_token

        monkeypatch.setattr(platform_settings, "ENABLE_AUTH", True)
        client = TestClient(app)
        headers = {
            "Authorization": f"Bearer {create_token({'sub': str(uuid4()), 'role': 'admin'})}"
        }
        for path in (_STATS_PATH, _CONSISTENCY_PATH):
            resp = client.get(path, headers=headers)
            assert resp.status_code == 200, path
            assert resp.json()["data"]["data_source"] == "demo_stub", path