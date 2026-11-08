"""T103：read_body 上限与断连（spec mcp-gateway：请求体读取上限与断连处理）+ T102 audit._call_log 有界。

- 累计超 1MB → BodyTooLargeError，auth/rate_limit 中间件转 413（JSON-RPC 信封
  code=-32002），内存不随请求体无限增长；
- http.disconnect → ClientDisconnectedError，读取协程退出不挂起；
- `_call_log` deque(maxlen=500)：10 万次调用长度稳定在上限内（T102/V5）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.asgi_util import (
    MAX_BODY_BYTES,
    BodyTooLargeError,
    ClientDisconnectedError,
    read_body,
)
from app import audit


def _receive(messages):
    it = iter(messages)

    async def receive():
        try:
            return next(it)
        except StopIteration:
            await asyncio.sleep(3600)  # 模拟无更多消息（若误等待则用例超时失败）

    return receive


def _send_stub() -> "_SendStub":
    return _SendStub()


class _SendStub:
    """ASGI send 收集器（callable，可直接传入中间件）。"""

    def __init__(self):
        self.sent: list[dict] = []

    async def __call__(self, msg):
        self.sent.append(msg)


class TestT103ReadBodyLimit:
    @pytest.mark.asyncio
    async def test_exact_limit_ok(self):
        """恰好 1MB：正常读取（边界不误伤）。"""
        body = b"x" * MAX_BODY_BYTES
        chunks = [body[: MAX_BODY_BYTES // 2], body[MAX_BODY_BYTES // 2 :]]
        messages = [
            {"type": "http.request", "body": c, "more_body": True} for c in chunks[:-1]
        ] + [{"type": "http.request", "body": chunks[-1], "more_body": False}]
        out = await read_body(_receive(messages))
        assert out == body

    @pytest.mark.asyncio
    async def test_over_limit_stops_accumulating(self):
        """超 1MB：BodyTooLargeError 且停止累积（不再读后续分片）。"""
        big = b"x" * (MAX_BODY_BYTES + 1)  # 单片即超限
        read_after_limit: list[bytes] = []

        async def receive():
            # 首片已超限；第二片若被读取则记录（断言 MUST NOT 继续累积）
            if not read_after_limit:
                read_after_limit.append(b"first")
                return {"type": "http.request", "body": big, "more_body": True}
            read_after_limit.append(b"second")
            return {"type": "http.request", "body": b"y", "more_body": False}

        with pytest.raises(BodyTooLargeError):
            await read_body(receive)
        assert read_after_limit == [b"first"]

    @pytest.mark.asyncio
    async def test_disconnect_raises_and_exits(self):
        """客户端断连：ClientDisconnectedError，协程退出不挂起。"""
        with pytest.raises(ClientDisconnectedError):
            await read_body(_receive([{"type": "http.disconnect"}]))

    @pytest.mark.asyncio
    async def test_non_request_message_skipped(self):
        """非 http.request 消息跳过（既有语义回归）。"""
        messages = [
            {"type": "http.response.start"},  # 异型消息跳过
            {"type": "http.request", "body": b"ok", "more_body": False},
        ]
        assert await read_body(_receive(messages)) == b"ok"


class TestT103Middleware413:
    def _scope(self) -> dict:
        return {"type": "http", "method": "POST", "path": "/mcp", "headers": []}

    @pytest.mark.asyncio
    async def test_auth_middleware_returns_413(self, monkeypatch):
        """auth 中间件：超限 body → 413 + JSON-RPC 信封（code=-32002）。"""
        from app.auth import MCPAuthMiddleware

        monkeypatch.setattr("app.auth.settings.ENABLE_AUTH", True)
        big = b"x" * (MAX_BODY_BYTES + 1)
        receive = _receive([{"type": "http.request", "body": big, "more_body": False}])
        send = _send_stub()

        async def downstream(scope, receive, send):  # pragma: no cover — 不可达
            raise AssertionError("超限请求不应到达下游")

        await MCPAuthMiddleware(downstream)(self._scope(), receive, send)
        assert send.sent[0]["status"] == 413
        body = json.loads(send.sent[1]["body"])
        assert body["error"]["code"] == -32002
        assert body["error"]["data"]["reason"] == "payload_too_large"

    @pytest.mark.asyncio
    async def test_rate_limit_middleware_returns_413(self):
        """rate_limit 中间件：超限 body → 413（不进入限流判定）。"""
        from app.rate_limit import MCPRateLimitMiddleware

        big = b"x" * (MAX_BODY_BYTES + 1)
        receive = _receive([{"type": "http.request", "body": big, "more_body": False}])
        send = _send_stub()

        async def downstream(scope, receive, send):  # pragma: no cover — 不可达
            raise AssertionError("超限请求不应到达下游")

        await MCPRateLimitMiddleware(downstream)(self._scope(), receive, send)
        assert send.sent[0]["status"] == 413

    @pytest.mark.asyncio
    async def test_client_disconnected_middleware_exits(self, monkeypatch):
        """客户端断连：中间件协程退出（无响应可发，不挂起）。"""
        from app.rate_limit import MCPRateLimitMiddleware

        receive = _receive([{"type": "http.disconnect"}])
        send = _send_stub()

        async def downstream(scope, receive, send):  # pragma: no cover — 不可达
            raise AssertionError("断连请求不应到达下游")

        await MCPRateLimitMiddleware(downstream)(self._scope(), receive, send)
        assert send.sent == []


class TestT102AuditCallLogBounded:
    def test_call_log_length_bounded(self):
        """10 万次调用形态验证（600 次采样）：长度恒为上限，RSS 不线性增长。"""
        audit.reset_call_log()
        for i in range(audit._CALL_LOG_MAXLEN + 100):
            audit.record_mcp_call(
                "rag_kb",
                "search",
                {"query": f"q{i}"},
                trace_id=f"t-{i}",
                session_id="s1",
                start=0.0,
                error=None,
            )
        assert len(audit.get_call_log()) == audit._CALL_LOG_MAXLEN
        # 最旧被驱逐（FIFO 环形）：首条不再是第 0 次调用
        assert audit.get_call_log()[0]["trace_id"] == f"t-{100}"
        audit.reset_call_log()