"""QC-1.4：统一信封与 A2 HTTP 映射（本轮无业务路由，用最小测试 app）。"""
from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.errors import (
    HTTP_STATUS_BY_CODE,
    envelope,
    is_business_success,
    register_qc_exception_handlers,
    success,
)
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware


class _ReplayProbe(BaseModel):
    call_ended: Literal[True]
    speaker: Literal["agent", "user"]
    turns: list[str] = Field(min_length=1)


def _probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceMiddleware)
    register_exception_handlers(app)
    register_qc_exception_handlers(app)

    @app.post("/_probe/validate")
    async def validate(body: _ReplayProbe):
        return success({"ok": True})

    @app.get("/_probe/biz/{code}")
    async def raise_biz(code: str):
        raise BizException(ErrorCode(code))

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_probe_app())


class TestEnvelope:
    def test_success_shape(self):
        body = success({"x": 1})
        assert set(body) == {"code", "message", "data", "trace_id"}
        assert body["code"] == "0"
        assert is_business_success(body)

    def test_envelope_keys(self):
        body = envelope(code="A2003", message="x", data=None)
        assert set(body) == {"code", "message", "data", "trace_id"}


class TestA2002Validation:
    def test_illegal_turns_is_422_a2002_not_b0001(self, client: TestClient):
        resp = client.post("/_probe/validate", json={"call_ended": True, "speaker": "agent", "turns": []})
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "A2002"
        assert "B0001" not in body.values()
        assert body["code"] != "B0001"
        assert set(body) == {"code", "message", "data", "trace_id"}

    def test_illegal_speaker_is_422_a2002(self, client: TestClient):
        resp = client.post(
            "/_probe/validate",
            json={"call_ended": True, "speaker": "robot", "turns": ["hi"]},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "A2002"
        assert "B0001" not in resp.text

    def test_call_ended_not_true_is_422_a2002(self, client: TestClient):
        resp = client.post(
            "/_probe/validate",
            json={"call_ended": False, "speaker": "agent", "turns": ["hi"]},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "A2002"
        assert "B0001" not in resp.text


class TestA2003BusinessFailure:
    def test_http_200_a2003_is_not_success(self, client: TestClient):
        resp = client.get("/_probe/biz/A2003")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "A2003"
        assert body["code"] != "0"
        assert is_business_success(body) is False


@pytest.mark.parametrize(
    "code,status",
    [
        ("A2001", 200),
        ("A2002", 422),
        ("A2003", 200),
        ("A2004", 400),
        ("A2005", 404),
        ("A2006", 409),
        ("A2007", 404),
        ("A2008", 400),
        ("A2009", 200),
        ("A2010", 200),
        ("A2011", 200),
        ("B0002", 401),
        ("B0003", 403),
    ],
)
def test_a2_http_status_map(client: TestClient, code: str, status: int):
    assert HTTP_STATUS_BY_CODE[code] == status
    resp = client.get(f"/_probe/biz/{code}")
    assert resp.status_code == status
    body = resp.json()
    assert body["code"] == code
    assert set(body) == {"code", "message", "data", "trace_id"}
    if code != "0":
        assert is_business_success(body) is False
