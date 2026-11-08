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

class TestTraceMiddlewarePureASGI:
    """T99：TraceMiddleware 纯 ASGI 形态守护与流式兼容。"""

    def test_not_basehttp_middleware(self):
        """MUST NOT 继承 BaseHTTPMiddleware（其 call_next 缓冲响应体破坏 SSE）。"""
        from starlette.middleware.base import BaseHTTPMiddleware

        assert not issubclass(TraceMiddleware, BaseHTTPMiddleware)
        assert not isinstance(TraceMiddleware, BaseHTTPMiddleware)

    @pytest.mark.asyncio
    async def test_sse_multi_chunk_forwarded_without_buffering(self):
        """SSE 多块 body MUST 逐条转发（无聚合、不读取响应体）。"""
        captured: list[dict] = []

        async def downstream_app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )
            await send({"type": "http.response.body", "body": b"data: chunk-1\n\n", "more_body": True})
            await send({"type": "http.response.body", "body": b"data: chunk-2\n\n", "more_body": True})
            await send({"type": "http.response.body", "body": b"data: chunk-3\n\n", "more_body": False})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            captured.append(message)

        await TraceMiddleware(downstream_app)(
            {"type": "http", "method": "GET", "path": "/mcp", "headers": []}, receive, send
        )

        starts = [m for m in captured if m["type"] == "http.response.start"]
        bodies = [m for m in captured if m["type"] == "http.response.body"]
        assert len(starts) == 1
        assert len(bodies) == 3
        assert bodies[0]["body"] == b"data: chunk-1\n\n" and bodies[0]["more_body"] is True
        assert bodies[1]["body"] == b"data: chunk-2\n\n" and bodies[1]["more_body"] is True
        assert bodies[2]["body"] == b"data: chunk-3\n\n" and bodies[2]["more_body"] is False
        header_map = dict(starts[0]["headers"])
        assert header_map[b"x-trace-id"]
        assert header_map[b"x-trace-id"].decode("latin-1") == get_trace_id()

    @pytest.mark.asyncio
    async def test_upstream_trace_header_replaced_with_contextvar_value(self):
        """上游已带 X-Trace-Id 头：替换为 contextvar 值（响应头与日志一致）。"""
        captured: list[dict] = []

        async def downstream_app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"x-trace-id", b"upstream-id")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            captured.append(message)

        await TraceMiddleware(downstream_app)(
            {"type": "http", "method": "GET", "path": "/x", "headers": []}, receive, send
        )
        header_map = dict(captured[0]["headers"])
        assert header_map[b"x-trace-id"] == get_trace_id().encode("latin-1")
        assert header_map[b"x-trace-id"] != b"upstream-id"

    def test_upstream_5xx_trace_id_still_written(self):
        """上游返回 5xx 响应（HTTPException 经内层转响应）时 trace_id 照常写入与回传。"""
        from fastapi import HTTPException

        app = FastAPI()
        app.add_middleware(TraceMiddleware)

        @app.get("/boom")
        async def boom():
            raise HTTPException(status_code=500, detail="upstream boom")

        client = TestClient(app)
        resp = client.get("/boom", headers={"X-Trace-Id": "trace-5xx"})
        assert resp.status_code == 500
        assert resp.headers["X-Trace-Id"] == "trace-5xx"

    @pytest.mark.asyncio
    async def test_non_http_scope_passthrough(self):
        """lifespan 等非 http scope 原样透传，不做 trace 处理。"""
        seen: list[dict] = []

        async def downstream_app(scope, receive, send):
            seen.append(scope)

        await TraceMiddleware(downstream_app)(
            {"type": "lifespan"},
            _noop_receive,
            _noop_send,
        )
        assert seen and seen[0]["type"] == "lifespan"


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(message) -> None:
    return None
