"""健康检查端点测试。"""
from fastapi.testclient import TestClient

from app.main import app


class TestHealth:
    def test_health_endpoint(self):
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "case-service"
        assert body["version"] == "1.0.0"

    def test_health_trace_id(self):
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Trace-Id": "trace_test"})
        assert resp.headers["X-Trace-Id"] == "trace_test"

    def test_api_v1_health(self):
        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
