"""f3-usage-console：/api/v1/llm-usage/summary|logs 契约与鉴权。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.routers.llm_usage import get_llm_usage_repo
from common.security.rbac import create_token


def _token(role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token({'sub': str(uuid4()), 'role': role})}"}


class FakeUsageRepo:
    def __init__(self) -> None:
        self.window: tuple[datetime, datetime] | None = None
        self.bucket: str | None = None
        self.log_args: dict = {}
        self.has_service = True

    async def totals(self, start, end):
        self.window = (start, end)
        return {
            "calls": 3,
            "tokens": 1500,
            "avg_latency_ms": 812.4,
            "failure_rate": 0.3333,
        }

    async def by_model(self, start, end):
        return [{"model": "deepseek-flash", "calls": 3, "tokens": 1500}]

    async def by_service(self, start, end):
        return [{"service": "qc", "calls": 3}] if self.has_service else []

    async def trend(self, start, end, bucket):
        self.bucket = bucket
        return [
            {
                "bucket_start": "2026-10-24T00:00:00+00:00",
                "calls": 3,
                "failure_rate": 0.3333,
            }
        ]

    async def logs(self, start, end, **kwargs):
        self.log_args = kwargs
        row = type(
            "Row",
            (),
            {
                "id": uuid4(),
                "trace_id": "tr_real_0001",
                "session_id": "sess-1",
                "provider": "deepseek",
                "model": "deepseek-flash",
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "latency_ms": 700,
                "confidence": 0.9,
                "status": "qc_review_exemption",
                "error_msg": None,
                "create_time": datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc),
            },
        )()
        return [row], 1


def _client(repo: FakeUsageRepo) -> TestClient:
    async def _repo():
        return repo

    app.dependency_overrides[get_llm_usage_repo] = _repo
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.clear()


class TestUsageSummary:
    def test_envelope_and_shape(self):
        repo = FakeUsageRepo()
        client = _client(repo)
        try:
            resp = client.get(
                "/api/v1/llm-usage/summary",
                params={
                    "start": "2026-10-17T00:00:00Z",
                    "end": "2026-10-24T00:00:00Z",
                },
                headers=_token(),
            )
        finally:
            _cleanup()
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["totals"]["calls"] == 3
        assert data["by_model"][0]["model"] == "deepseek-flash"
        assert data["by_service"] == [{"service": "qc", "calls": 3}]
        # 7 天跨度 → 按天分桶
        assert repo.bucket == "day"
        assert len(data["trend"]) == 1
        # resilience 未落库 → 不返回，前端整块隐藏
        assert "resilience" not in data

    def test_by_service_absent_when_no_rows(self):
        repo = FakeUsageRepo()
        repo.has_service = False
        client = _client(repo)
        try:
            resp = client.get("/api/v1/llm-usage/summary", headers=_token())
        finally:
            _cleanup()
        assert resp.status_code == 200
        assert "by_service" not in resp.json()["data"]

    def test_short_window_uses_hour_bucket(self):
        repo = FakeUsageRepo()
        client = _client(repo)
        try:
            resp = client.get(
                "/api/v1/llm-usage/summary",
                params={
                    "start": "2026-10-23T00:00:00Z",
                    "end": "2026-10-24T00:00:00Z",
                },
                headers=_token("supervisor"),
            )
        finally:
            _cleanup()
        assert resp.status_code == 200
        assert repo.bucket == "hour"

    def test_invalid_window_rejected(self):
        repo = FakeUsageRepo()
        client = _client(repo)
        try:
            resp = client.get(
                "/api/v1/llm-usage/summary",
                params={
                    "start": "2026-10-24T00:00:00Z",
                    "end": "2026-10-17T00:00:00Z",
                },
                headers=_token(),
            )
        finally:
            _cleanup()
        assert resp.status_code == 422
        assert resp.json()["code"] == "A2002"


class TestUsageLogs:
    def test_paging_and_filters_passthrough(self):
        repo = FakeUsageRepo()
        client = _client(repo)
        try:
            resp = client.get(
                "/api/v1/llm-usage/logs",
                params={
                    "page": 2,
                    "page_size": 50,
                    "status": "qc_review_exemption",
                    "provider": "deepseek",
                    "trace_id": "tr_real_0001",
                },
                headers=_token(),
            )
        finally:
            _cleanup()
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["page"] == 2 and data["page_size"] == 50 and data["total"] == 1
        item = data["items"][0]
        assert item["status"] == "qc_review_exemption"
        assert item["token_usage"]["total_tokens"] == 15
        assert item["create_time"].startswith("2026-10-24T01:00:00")
        assert repo.log_args["status"] == "qc_review_exemption"
        assert repo.log_args["provider"] == "deepseek"
        assert repo.log_args["trace_id"] == "tr_real_0001"

    def test_page_size_upper_bound(self):
        client = _client(FakeUsageRepo())
        try:
            resp = client.get(
                "/api/v1/llm-usage/logs", params={"page_size": 101}, headers=_token()
            )
        finally:
            _cleanup()
        assert resp.status_code == 422


class TestRoles:
    def test_agent_forbidden(self):
        client = _client(FakeUsageRepo())
        try:
            resp = client.get("/api/v1/llm-usage/summary", headers=_token("agent"))
        finally:
            _cleanup()
        assert resp.status_code == 403
        assert resp.json()["code"] == "B0003"

    def test_missing_token_unauthorized(self):
        client = _client(FakeUsageRepo())
        try:
            resp = client.get("/api/v1/llm-usage/summary")
        finally:
            _cleanup()
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"
