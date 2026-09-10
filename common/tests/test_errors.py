"""common.errors 模块单元测试。"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.errors.exceptions import BizException, SysException
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware


class TestErrorCodes:
    def test_error_code_count(self):
        """错误码总数：A1(13)+A2(11)+A3(6)+A4(15)+A5(14)+A6(15)+A7(6)+B(10)=90。"""
        codes = [c for c in ErrorCode]
        assert len(codes) == 90

    def test_all_codes_have_messages(self):
        """每个错误码都有对应的消息。"""
        for code in ErrorCode:
            assert code.value in ERROR_MESSAGES
            assert len(ERROR_MESSAGES[code.value]) > 0

    def test_code_ranges(self):
        """验证各域错误码范围。"""
        a1_codes = [c for c in ErrorCode if c.value.startswith("A1")]
        a2_codes = [c for c in ErrorCode if c.value.startswith("A2")]
        a4_codes = [c for c in ErrorCode if c.value.startswith("A4")]
        b_codes = [c for c in ErrorCode if c.value.startswith("B")]
        assert len(a1_codes) == 13
        assert len(a2_codes) == 11
        assert len(a4_codes) == 15
        assert len(b_codes) == 10


class TestExceptions:
    def test_biz_exception_with_message(self):
        exc = BizException(ErrorCode.A1001, "自定义消息")
        assert exc.code == ErrorCode.A1001
        assert exc.message == "自定义消息"

    def test_biz_exception_default_message(self):
        exc = BizException(ErrorCode.A4001)
        assert exc.code == ErrorCode.A4001
        assert exc.message == ERROR_MESSAGES["A4001"]

    def test_sys_exception_default(self):
        exc = SysException()
        assert exc.code == ErrorCode.B0007

    def test_sys_exception_custom_code(self):
        exc = SysException(ErrorCode.B0009, "服务不可用")
        assert exc.code == ErrorCode.B0009
        assert exc.message == "服务不可用"


class TestExceptionHandlers:
    @pytest.fixture()
    def app(self):
        app = FastAPI()
        app.add_middleware(TraceMiddleware)
        register_exception_handlers(app)

        @app.get("/biz-error")
        async def _biz():
            raise BizException(ErrorCode.A1001, "非工作时段")

        @app.get("/sys-error")
        async def _sys():
            raise SysException(ErrorCode.B0007, "内部错误")

        @app.get("/unexpected")
        async def _unexpected():
            raise RuntimeError("unexpected")

        @app.get("/ok")
        async def _ok():
            return {"code": "0", "message": "ok", "data": None}

        return app

    def test_biz_exception_response(self, app):
        client = TestClient(app)
        resp = client.get("/biz-error")
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "A1001"
        assert body["message"] == "非工作时段"
        assert body["data"] is None
        assert "trace_id" in body

    def test_sys_exception_response(self, app):
        client = TestClient(app)
        resp = client.get("/sys-error")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "B0007"
        assert body["data"] is None

    def test_unexpected_exception_response(self, app):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/unexpected")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "B0007"

    def test_trace_id_in_response_header(self, app):
        client = TestClient(app)
        resp = client.get("/ok", headers={"X-Trace-Id": "trace_test_001"})
        assert resp.headers.get("X-Trace-Id") == "trace_test_001"

    def test_trace_id_auto_generated(self, app):
        client = TestClient(app)
        resp = client.get("/ok")
        trace_id = resp.headers.get("X-Trace-Id", "")
        assert len(trace_id) > 0
