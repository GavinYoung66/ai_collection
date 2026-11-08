"""T87：qc demo 路由组闸门与 qc_reviewer token 签发显式开关。

- 生产（auth_required=true）整组禁用 → 404，禁用形态优先于角色校验，无灌数副作用
- 本地（auth_required=false）按 require_roles_if_enabled("admin") 语义放行（调试可用性）
- DEMO_MODE 缺省 false：seed 响应不含 token；开启且 admin：灌数 + pending + trace 全流程可用
- 401/403 为闸门 admin 校验分支的机制级行为（B0002/B0003），见 TestAdminCheckerSemantics
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import ForbiddenError, UnauthorizedError, require_roles
from app.core.config import qc_settings
from app.main import app
from app.repositories.demo_store import DemoSessionStore
from app.repositories.job_store import QcJobStore
from app.deps import get_quality_repo, get_redis
from app.routers.demo import get_demo_seed_service
from app.services.demo_seed_service import DemoSeedService
from common.config.settings import settings
from common.db.session import get_session
from common.errors.codes import ERROR_MESSAGES
from common.security.rbac import create_token, verify_token
from tests.test_job_store import MemoryRedis
from tests.test_replay_api import ApiFakeSession, FakeQualityRepo
from tests.test_demo_seed import FakeDemoRepo

_DEMO_SEED = "/api/v1/qc/demo/seed"
_DEMO_PENDING = "/api/v1/qc/demo/pending"


class _FakeRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers
        self.state = SimpleNamespace()


def _client(
    session: ApiFakeSession,
    redis: MemoryRedis,
    demo_repo: FakeDemoRepo,
    quality: FakeQualityRepo,
) -> TestClient:
    svc = DemoSeedService(demo_repo, DemoSessionStore(redis), quality)

    async def _demo():
        return svc

    async def _session():
        yield session

    async def _redis():
        return redis

    async def _quality():
        return quality

    app.dependency_overrides[get_demo_seed_service] = _demo
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    app.dependency_overrides[get_quality_repo] = _quality
    return TestClient(app)


def _setup(monkeypatch, *, enable_auth: bool, demo_mode: bool):
    monkeypatch.setattr(settings, "ENABLE_AUTH", enable_auth)
    monkeypatch.setattr(qc_settings, "DEMO_MODE", demo_mode)
    session = ApiFakeSession()
    redis = MemoryRedis(db=3)
    demo_repo = FakeDemoRepo(session)
    quality = FakeQualityRepo(session, QcJobStore(redis))
    client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality)
    return client, demo_repo, session


class TestT87ProductionDisabled:
    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_production_seed_returns_404_without_side_effects(self, monkeypatch):
        client, demo_repo, session = _setup(monkeypatch, enable_auth=True, demo_mode=True)
        resp = client.post(_DEMO_SEED)
        assert resp.status_code == 404
        assert "token" not in resp.json()
        assert demo_repo.calls == []
        assert not session.call_ids

    def test_production_all_three_endpoints_blocked(self, monkeypatch):
        client, demo_repo, _ = _setup(monkeypatch, enable_auth=True, demo_mode=True)
        sid = f"qc_demo_{uuid4().hex}"
        paths = [
            ("post", _DEMO_SEED),
            ("get", _DEMO_PENDING),
            ("get", f"/api/v1/qc/demo/{sid}/trace"),
        ]
        for method, path in paths:
            resp = getattr(client, method)(path)
            assert resp.status_code == 404, f"{method} {path}"
        assert demo_repo.calls == []

    def test_production_low_privilege_token_still_404_gate_first(self, monkeypatch):
        client, demo_repo, _ = _setup(monkeypatch, enable_auth=True, demo_mode=True)
        token = create_token({"sub": str(uuid4()), "role": "agent"})
        resp = client.post(
            _DEMO_SEED, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 404
        assert demo_repo.calls == []


class TestT87LocalMode:
    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_seed_without_demo_mode_has_no_token_field(self, monkeypatch):
        client, demo_repo, session = _setup(monkeypatch, enable_auth=False, demo_mode=False)
        resp = client.post(_DEMO_SEED)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "token" not in data
        assert len(data["session_ids"]) == 3
        assert len(demo_repo.calls) == 3
        UUID(data["reviewer_id"])

    def test_demo_mode_full_flow_with_token(self, monkeypatch):
        client, _, _ = _setup(monkeypatch, enable_auth=False, demo_mode=True)
        seed = client.post(_DEMO_SEED)
        assert seed.status_code == 200
        data = seed.json()["data"]
        assert "token" in data
        payload = verify_token(data["token"])
        assert payload is not None
        assert payload["role"] == "qc_reviewer"
        assert payload["sub"] == data["reviewer_id"]

        pending = client.get(_DEMO_PENDING)
        assert pending.status_code == 200
        assert pending.json()["data"]["total"] == 3

        trace = client.get(f"/api/v1/qc/demo/{data['session_ids'][0]}/trace")
        assert trace.status_code == 200
        assert trace.json()["data"]["session_id"] == data["session_ids"][0]


class TestAdminCheckerSemantics:
    """闸门 admin 校验分支的机制级行为（spec 5.1.3 场景 1/2：401 B0002 / 403 B0003）。"""

    def test_anonymous_rejected_with_b0002(self):
        checker = require_roles("admin")
        with pytest.raises(UnauthorizedError) as exc_info:
            asyncio.run(checker(_FakeRequest({})))
        assert exc_info.value.message == ERROR_MESSAGES["B0002"]

    def test_low_privilege_role_rejected_with_b0003(self):
        checker = require_roles("admin")
        token = create_token({"sub": str(uuid4()), "role": "qc_reviewer"})
        request = _FakeRequest({"Authorization": f"Bearer {token}"})
        with pytest.raises(ForbiddenError) as exc_info:
            asyncio.run(checker(request))
        assert exc_info.value.message == ERROR_MESSAGES["B0003"]