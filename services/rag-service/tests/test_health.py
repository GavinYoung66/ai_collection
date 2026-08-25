"""健康检查端点测试（rag-service-final-fix 3.4：根路径与 /api/v1 共用探活实现）。"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

PROBE_RESULT = {
    "status": "healthy",
    "service": "rag-service",
    "version": "1.0.0",
    "checks": {
        "database": "ok", "milvus": "ok", "redis": "ok", "embedding": "ok",
    },
    "ts": 1724054400,
}


class TestHealth:
    def _probe_patch(self):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch("app.api.health.probe_health", return_value=PROBE_RESULT)
        )
        stack.enter_context(
            patch("app.main.probe_health", return_value=PROBE_RESULT)
        )
        return stack

    def test_health_endpoint(self):
        client = TestClient(app)
        with self._probe_patch():
            resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "rag-service"
        assert body["version"] == "1.0.0"
        assert body["checks"] == PROBE_RESULT["checks"]
        assert "ts" in body

    def test_health_trace_id(self):
        client = TestClient(app)
        with self._probe_patch():
            resp = client.get("/health", headers={"X-Trace-Id": "trace_test"})
        assert resp.headers["X-Trace-Id"] == "trace_test"

    def test_api_v1_health(self):
        client = TestClient(app)
        with self._probe_patch():
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == PROBE_RESULT
