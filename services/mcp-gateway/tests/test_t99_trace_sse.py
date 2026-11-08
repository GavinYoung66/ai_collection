"""T99：TraceMiddleware 纯 ASGI 改造——mcp-gateway 挂载面 SSE 流式兼容用例。

- /mcp 型 SSE 多块响应经中间件逐块到达客户端（逐块时序、无聚合延迟）
- 普通 JSON 响应 X-Trace-Id 头照常注入且与日志 trace_id 一致
- 上游 5xx 错误响应 trace_id 照常写入与回传
- /health、/metrics 豁免路径回归（中间件栈序 Metrics→Trace→Auth→RateLimit 禁改）
"""
from __future__ import annotations

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from common.trace.middleware import TraceMiddleware, get_trace_id


def _stream_app() -> Starlette:
    """挂 TraceMiddleware 的最小流式探测应用（/sse + /json + /boom + /health）。"""

    async def sse_stub(request):  # noqa: ARG001
        async def gen():
            yield f'data: {json.dumps({"n": 1, "trace": get_trace_id()})}\n\n'
            yield f'data: {json.dumps({"n": 2, "trace": get_trace_id()})}\n\n'
            yield f'data: {json.dumps({"n": 3, "trace": get_trace_id()})}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def json_stub(request):  # noqa: ARG001
        return JSONResponse({"trace_id": get_trace_id()})

    async def boom_stub(request):  # noqa: ARG001
        from starlette.exceptions import HTTPException as StarletteHTTPException

        raise StarletteHTTPException(status_code=500, detail="upstream boom")

    async def health_stub(request):  # noqa: ARG001
        return JSONResponse({"ok": True})

    star = Starlette(
        routes=[
            Route("/sse", sse_stub, methods=["GET"]),
            Route("/json", json_stub, methods=["GET"]),
            Route("/boom", boom_stub, methods=["GET"]),
            Route("/health", health_stub, methods=["GET"]),
        ]
    )
    star.add_middleware(TraceMiddleware)
    return star


@pytest.fixture()
def transport():
    return httpx.ASGITransport(app=_stream_app())


@pytest.mark.asyncio
async def test_sse_multi_chunk_arrives_incrementally():
    """SSE 多块响应经实际 Starlette app + TraceMiddleware 逐块发送（ASGI 消息级断言）。

    逐块性以 send 消息序列为准：客户端 SDK（httpx 等）可能合并网络 chunk，
    不能作为中间件聚合与否的证据。
    """
    captured: list[dict] = []

    async def receive():
        # StreamingResponse 会循环 receive 等待 http.disconnect（listen_for_disconnect）；
        # 首次返回请求体、随后返回 disconnect，避免 task_group 永不退出
        if not receive._answered:  # type: ignore[attr-defined]
            receive._answered = True  # type: ignore[attr-defined]
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    receive._answered = False  # type: ignore[attr-defined]

    async def send(message):
        captured.append(message)

    await _stream_app()(
        {"type": "http", "method": "GET", "path": "/sse", "headers": [(b"x-trace-id", b"trace-sse-1")]},
        receive,
        send,
    )

    starts = [m for m in captured if m["type"] == "http.response.start"]
    bodies = [m for m in captured if m["type"] == "http.response.body"]
    assert len(starts) == 1
    assert starts[0]["status"] == 200
    header_map = dict(starts[0]["headers"])
    assert header_map[b"x-trace-id"] == b"trace-sse-1"
    # 三个数据块逐条转发、顺序保持 + 末尾空块结束标记（more_body=False，无聚合延迟）
    assert len(bodies) == 4
    decoded = [m["body"].decode("utf-8") for m in bodies[:3]]
    assert decoded[0].startswith('data: {"n": 1')
    assert decoded[1].startswith('data: {"n": 2')
    assert decoded[2].startswith('data: {"n": 3')
    assert bodies[0]["more_body"] is True and bodies[1]["more_body"] is True
    assert bodies[2]["more_body"] is True
    assert bodies[3]["body"] == b"" and bodies[3]["more_body"] is False


@pytest.mark.asyncio
async def test_sse_full_payload_intact_over_http_client(transport):
    """客户端级回归：SSE 响应内容完整、媒体类型正确（逐块性见上一用例消息级断言）。"""
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/sse", headers={"X-Trace-Id": "trace-sse-1"})
    assert resp.status_code == 200
    assert resp.headers["x-trace-id"] == "trace-sse-1"
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"n": 1' in resp.text and 'data: {"n": 3' in resp.text


@pytest.mark.asyncio
async def test_json_response_trace_header_injected(transport):
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/json", headers={"X-Trace-Id": "trace-json-1"})
    assert resp.status_code == 200
    assert resp.headers["x-trace-id"] == "trace-json-1"
    assert resp.json()["trace_id"] == "trace-json-1"


@pytest.mark.asyncio
async def test_auto_generated_trace_on_missing_header(transport):
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/json")
    assert resp.status_code == 200
    header_trace = resp.headers["x-trace-id"]
    assert header_trace
    assert resp.json()["trace_id"] == header_trace


@pytest.mark.asyncio
async def test_upstream_5xx_trace_id_written(transport):
    """上游返回 5xx 响应（HTTPException 经内层转响应）时 trace_id 照常写入与回传。"""
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/boom", headers={"X-Trace-Id": "trace-5xx-gw"})
    assert resp.status_code == 500
    assert resp.headers["x-trace-id"] == "trace-5xx-gw"


@pytest.mark.asyncio
async def test_health_not_broken_by_middleware(transport):
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}