"""健康检查端点测试。权威探活为服务根 GET /health。"""
from fastapi.testclient import TestClient

from app.main import app


class TestHealth:
    def test_health_endpoint(self):
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "qc-service"
        assert body["version"] == "1.0.0"

    def test_health_trace_id(self):
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Trace-Id": "trace_test"})
        assert resp.headers["X-Trace-Id"] == "trace_test"

    def test_health_not_mounted_under_api_v1(self):
        client = TestClient(app)
        assert client.get("/api/v1/health").status_code == 404
