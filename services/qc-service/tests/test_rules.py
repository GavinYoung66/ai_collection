"""QC-8.4：规则版本 GET active 与原子 apply。"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.deps import get_job_store, get_rule_repo
from app.main import app
from app.models.qc import QcRule
from app.repositories.rule_repo import RuleRepo
from app.seeds.qc_rules_v1 import DEDUCTION_POLICY_V1, PENALTY_V1
from common.security.rbac import create_token
from tests.test_job_store import MemoryRedis
from app.repositories.job_store import QcJobStore


def _token(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token({'sub': str(uuid4()), 'role': role})}"}


def _policy(*exclude: str) -> dict:
    policy = {
        f"R{i:02d}": {"clause_no": "t", "clause_text": "t"} for i in range(1, 16)
    }
    policy["__ac_default__"] = {"by_redline_tag": {}, "by_severity": {}}
    for rid in exclude:
        policy.pop(rid, None)
    return policy


def _rule(version: str, status: str, *, penalty=None, policy=None, window: int = 30000, rules=None) -> QcRule:
    row = QcRule(
        version=version,
        status=status,
        rules=rules if rules is not None else [],
        penalty=penalty if penalty is not None else dict(PENALTY_V1),
        same_word_window_ms=window,
        deduction_policy=policy if policy is not None else _policy(),
    )
    row.id = uuid4()
    row.is_delete = "0"
    return row


class FakeCommitSession:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.rolled = False

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        if self.fail:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rolled = True


class FakeRuleRepo(RuleRepo):
    def __init__(self, rules: list[QcRule], *, fail_commit: bool = False) -> None:
        self.session = FakeCommitSession(fail=fail_commit)
        self._rules = rules

    async def get_active(self) -> QcRule | None:
        for row in self._rules:
            if row.status == "active" and getattr(row, "is_delete", "0") == "0":
                return row
        return None

    async def get_by_version(self, version: str) -> QcRule | None:
        for row in self._rules:
            if row.version == version and getattr(row, "is_delete", "0") == "0":
                return row
        return None


def _client(repo: FakeRuleRepo, redis: MemoryRedis | None = None) -> TestClient:
    redis = redis or MemoryRedis(db=3)
    store = QcJobStore(redis)

    async def _repo():
        return repo

    async def _store():
        return store

    app.dependency_overrides[get_rule_repo] = _repo
    app.dependency_overrides[get_job_store] = _store
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.clear()


class TestRulesHttp:
    def teardown_method(self):
        _cleanup()

    def test_get_active_returns_only_active_package(self):
        repo = FakeRuleRepo([_rule("v1.0", "active"), _rule("v0.9", "archived")])
        client = _client(repo)
        resp = client.get("/api/v1/qc/rules/active", headers=_token("ai_trainer"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == "v1.0"
        assert data["status"] == "active"
        assert data["penalty"]["high"] == 20
        assert data["same_word_window_ms"] == 30000
        assert "grade_thresholds" in data
        assert "deduction_policy" in data
        assert "effective_at" in data

    def test_get_active_missing_is_a2009_http_200(self):
        client = _client(FakeRuleRepo([]))
        resp = client.get("/api/v1/qc/rules/active", headers=_token("ADMIN"))
        assert resp.status_code == 200
        assert resp.json()["code"] == "A2009"

    def test_illegal_version_keeps_previous_active(self):
        v1 = _rule("v1.0", "active")
        bad = _rule("v9.9", "draft", penalty={})
        repo = FakeRuleRepo([v1, bad])
        client = _client(repo)
        resp = client.post("/api/v1/qc/rules/apply", headers=_token("ADMIN"), json={"version": "v9.9"})
        assert resp.status_code == 200
        assert resp.json()["code"] == "A2009"
        assert v1.status == "active"
        assert bad.status == "draft"
        missing = client.post("/api/v1/qc/rules/apply", headers=_token("ADMIN"), json={"version": "nope"})
        assert missing.status_code == 200
        assert missing.json()["code"] == "A2009"
        assert v1.status == "active"

    def test_apply_rejects_package_missing_deduction_policy(self):
        v1 = _rule("v1.0", "active")
        bad_policy = {k: v for k, v in DEDUCTION_POLICY_V1.items() if k != "R07"}
        bad = _rule(
            "v9.8",
            "draft",
            policy=bad_policy,
            rules=[{"rule_id": "R07", "type": "keyword", "enabled": True, "severity": "medium", "params": {}}],
        )
        repo = FakeRuleRepo([v1, bad])
        client = _client(repo)
        resp = client.post("/api/v1/qc/rules/apply", headers=_token("ADMIN"), json={"version": "v9.8"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "A2009"
        assert "R07" in body["message"]
        assert v1.status == "active"
        assert bad.status == "draft"

    def test_commit_failure_is_a2010_and_does_not_switch(self):
        v1 = _rule("v1.0", "active")
        v2 = _rule("v1.1", "draft")
        repo = FakeRuleRepo([v1, v2], fail_commit=True)
        client = _client(repo)
        resp = client.post("/api/v1/qc/rules/apply", headers=_token("ADMIN"), json={"version": "v1.1"})
        assert resp.status_code == 200
        assert resp.json()["code"] == "A2010"
        assert v1.status == "active"
        assert v2.status == "draft"
        assert repo.session.rolled is True

    def test_apply_success_and_idempotent_same_version(self):
        v1 = _rule("v1.0", "active")
        v2 = _rule("v1.1", "draft")
        repo = FakeRuleRepo([v1, v2])
        redis = MemoryRedis(db=3)
        client = _client(repo, redis)
        first = client.post("/api/v1/qc/rules/apply", headers=_token("ADMIN"), json={"version": "v1.1"})
        assert first.status_code == 200
        assert first.json()["code"] == "0"
        data = first.json()["data"]
        assert data["version"] == "v1.1"
        assert data["status"] == "active"
        assert data["previous_version"] == "v1.0"
        assert v1.status == "archived"
        assert v2.status == "active"
        again = client.post("/api/v1/qc/rules/apply", headers=_token("admin"), json={"version": "v1.1"})
        assert again.status_code == 200
        assert again.json()["data"]["version"] == "v1.1"
        assert v2.status == "active"
        actives = [r for r in repo._rules if r.status == "active"]
        assert len(actives) == 1

    def test_unauthenticated_is_401(self):
        client = _client(FakeRuleRepo([_rule("v1.0", "active")]))
        resp = client.get("/api/v1/qc/rules/active")
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"
