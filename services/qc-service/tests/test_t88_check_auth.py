"""T88：qc check 三写端点鉴权与限流补齐。

- 未认证（auth_required=true）三端点 → 401 B0002，零副作用（DP 零调用、无明细写入、无事件发布）
- 低权限角色（agent token）→ 403 B0003
- sid/batch 高频 → 429 B0006（键 qc:ratelimit:sid|batch:*）
- session_ids 超 QC_BATCH_MAX → A2004（鉴权限流先行后仍保持）
- auth_required=false 本地放行调试语义保持；admin/supervisor 正常执行
"""
from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.deps import get_redis
from app.integrations.qc_session import QCSession
from app.main import app
from app.repositories.job_store import QcJobStore
from app.routers.check import get_dp_client, get_llm_reviewer, get_quality_repo
from app.services.llm_reviewer import LLMReviewer
from common.config.settings import settings as platform_settings
from common.db.session import get_session
from common.security.rbac import create_token
from tests.test_job_store import MemoryRedis
from tests.test_replay_api import (
    ApiFakeSession,
    FakeDp,
    FakeQualityRepo,
    _empty_llm,
    _ready,
)

_REPLAY_RAW = "/api/v1/qc/replay/raw"
_BATCH_MAX = 100


class _CountingLLM(LLMReviewer):
    def __init__(self):
        self.calls = 0
        super().__init__(invoke=self._count)

    async def _count(self, _messages):
        self.calls += 1
        return "[]"


def _client(db: ApiFakeSession, redis: MemoryRedis, dp: FakeDp, llm) -> TestClient:
    async def _session():
        yield db

    async def _redis():
        return redis

    def _llm():
        return llm

    async def _quality():
        return FakeQualityRepo(db, QcJobStore(redis))

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    app.dependency_overrides[get_dp_client] = lambda: dp
    app.dependency_overrides[get_llm_reviewer] = _llm
    app.dependency_overrides[get_quality_repo] = _quality
    return TestClient(app)



def _cleanup():
    from common.resilience._redis import reset_redis

    app.dependency_overrides.clear()
    reset_redis()


class TestT88AuthRequired:
    def teardown_method(self):
        _cleanup()

    def test_unauthenticated_three_write_endpoints_401_zero_side_effects(self, monkeypatch):
        monkeypatch.setattr(platform_settings, "ENABLE_AUTH", True)
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        llm = _CountingLLM()
        client = _client(db, redis, dp, llm)

        raw_resp = client.post(_REPLAY_RAW, headers={"X-Idempotency-Key": "k1"}, json=_ready())
        sid_resp = client.post("/api/v1/qc/replay/sess_ok", json={})
        batch_resp = client.post("/api/v1/qc/check/batch", json={"batch_id": "b1", "session_ids": ["sess_ok"]})

        for resp in (raw_resp, sid_resp, batch_resp):
            assert resp.status_code == 401, resp.text
            body = resp.json()
            assert body["code"] == "B0002"
            assert body.get("data") is None or "token" not in body["data"]
        assert dp.calls == []
        assert llm.calls == 0
        assert db.added == []

    def test_low_privilege_role_403(self, monkeypatch):
        monkeypatch.setattr(platform_settings, "ENABLE_AUTH", True)
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        client = _client(db, redis, dp, _CountingLLM())
        token = create_token({"sub": str(uuid4()), "role": "agent"})
        headers = {"Authorization": f"Bearer {token}"}

        raw_resp = client.post(_REPLAY_RAW, headers={**headers, "X-Idempotency-Key": "k1"}, json=_ready())
        sid_resp = client.post("/api/v1/qc/replay/sess_ok", headers=headers, json={})
        batch_resp = client.post(
            "/api/v1/qc/check/batch", headers=headers, json={"batch_id": "b1", "session_ids": ["sess_ok"]}
        )
        for resp in (raw_resp, sid_resp, batch_resp):
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "B0003"
        assert dp.calls == []


class TestT88RateLimit:
    def teardown_method(self):
        _cleanup()

    def test_replay_sid_eleventh_is_b0006(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        client = _client(db, redis, dp, _CountingLLM())
        codes = []
        for i in range(11):
            resp = client.post(
                "/api/v1/qc/replay/sess_ok",
                headers={"X-User-Id": "u-sid-rate", "X-QC-Force-Rerun": "true"},
                json={},
            )
            codes.append((resp.status_code, resp.json()["code"]))
        assert codes[:10] == [(200, "0")] * 10
        assert codes[10] == (429, "B0006")

    def test_batch_eleventh_is_b0006(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        client = _client(db, redis, dp, _CountingLLM())
        codes = []
        for i in range(11):
            resp = client.post(
                "/api/v1/qc/check/batch",
                headers={"X-User-Id": "u-batch-rate"},
                json={"batch_id": f"b{i}", "session_ids": ["sess_ok"]},
            )
            codes.append((resp.status_code, resp.json()["code"]))
        assert codes[10] == (429, "B0006")
        assert codes[0][1] != "B0006"

    def test_rate_limit_keys_share_namespace(self):
        """T88：sid/batch 限流键与 raw 同 qc:ratelimit:* 命名空间。"""
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        client = _client(db, redis, dp, _CountingLLM())
        for i in range(11):
            client.post(
                "/api/v1/qc/check/batch",
                headers={"X-User-Id": "u-ns"},
                json={"batch_id": f"b{i}", "session_ids": ["sess_ok"]},
            )
        # 未认证（auth_required=false 本地放行）：X-User-Id 作为用户维
        assert "qc:ratelimit:batch:u-ns" in redis.data
        assert not any(key.startswith("qc:ratelimit:raw:") for key in redis.data)


class TestT88OrderAndLocalMode:
    def teardown_method(self):
        _cleanup()

    def test_batch_over_max_returns_a2004_after_auth(self, monkeypatch):
        monkeypatch.setattr(platform_settings, "ENABLE_AUTH", True)
        db = ApiFakeSession()
        redis = MemoryRedis(db=3)
        dp = FakeDp({})
        client = _client(db, redis, dp, _CountingLLM())
        token = create_token({"sub": str(uuid4()), "role": "admin"})
        resp = client.post(
            "/api/v1/qc/check/batch",
            headers={"Authorization": f"Bearer {token}"},
            json={"batch_id": "b1", "session_ids": [f"s{i}" for i in range(_BATCH_MAX + 1)]},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "A2004"
        assert dp.calls == []

    def test_local_mode_keeps_debug_access_without_token(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_wait")
        redis = MemoryRedis(db=3)
        qc = QCSession.model_validate(
            {
                "session_id": "sess_wait",
                "case_id": "c1",
                "call_ended": False,
                "text_ready": False,
                "turns": [],
                "result": None,
            }
        )
        dp = FakeDp({"sess_wait": qc})
        client = _client(db, redis, dp, _CountingLLM())
        assert not platform_settings.auth_required
        resp = client.post("/api/v1/qc/replay/sess_wait", json={})
        assert resp.status_code == 200
        assert resp.json()["code"] == "A2003"
        assert dp.calls == ["sess_wait"]

    def test_admin_and_supervisor_can_execute(self, monkeypatch):
        monkeypatch.setattr(platform_settings, "ENABLE_AUTH", True)
        db = ApiFakeSession()
        db.call_ids.add("sess_wait")
        redis = MemoryRedis(db=3)
        qc = QCSession.model_validate(
            {
                "session_id": "sess_wait",
                "case_id": "c1",
                "call_ended": False,
                "text_ready": False,
                "turns": [],
                "result": None,
            }
        )
        dp = FakeDp({"sess_wait": qc})
        client = _client(db, redis, dp, _CountingLLM())
        for role in ("admin", "supervisor"):
            token = create_token({"sub": str(uuid4()), "role": role})
            resp = client.post(
                "/api/v1/qc/replay/sess_wait",
                headers={"Authorization": f"Bearer {token}", "X-QC-Force-Rerun": "true"},
                json={},
            )
            assert resp.status_code == 200, f"{role}: {resp.text}"
            assert resp.json()["code"] == "A2003"
        assert dp.calls == ["sess_wait", "sess_wait"]