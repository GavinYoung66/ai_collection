"""common.trace 模块单元测试。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from common.trace.middleware import TraceMiddleware, get_trace_id, set_trace_id


class TestTraceId:
    def test_get_set_trace_id(self):
        set_trace_id("trace_abc123")
        assert get_trace_id() == "trace_abc123"
        set_trace_id("")

    def test_default_empty(self):
        set_trace_id("")
        assert get_trace_id() == ""

    def test_overwrite(self):
        set_trace_id("first")
        set_trace_id("second")
        assert get_trace_id() == "second"
        set_trace_id("")


class TestTraceMiddleware:
    @pytest.fixture()
    def app(self):
        app = FastAPI()
        app.add_middleware(TraceMiddleware)

        @app.get("/echo")
        async def echo(request: Request):
            return {"trace_id": get_trace_id()}

        return app

    def test_trace_id_passthrough(self, app):
        client = TestClient(app)
        resp = client.get("/echo", headers={"X-Trace-Id": "trace_xyz"})
        assert resp.status_code == 200
        assert resp.json()["trace_id"] == "trace_xyz"
        assert resp.headers["X-Trace-Id"] == "trace_xyz"

    def test_trace_id_auto_generated(self, app):
        client = TestClient(app)
        resp = client.get("/echo")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["trace_id"]) > 0
        assert body["trace_id"] != ""
        assert resp.headers["X-Trace-Id"] == body["trace_id"]
