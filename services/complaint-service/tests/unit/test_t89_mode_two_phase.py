"""T89：complaint mode 组鉴权与两阶段双签审批。

- 匿名/未认证 PUT /complaints/mode → 401/403，全局模式键不写入、事件不发布
- 请求体 signers=["ops","tech"] 自报不生效（字段保留但忽略，不作判定依据）
- 两个不同已认证身份先后审批且参数一致 → 切换成功、审批记录可追溯、待审批记录清空
- 同身份两次审批 → 不推进；第二阶段参数与快照不一致 → 旧记录拒绝并重新登记
- Redis 异常 → A5014 回滚（T40 warning 语义由 test_t40 覆盖 _advance 路径）
- full_switch_if_consistent 服务内路径行为回归
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.mode import set_redis_client
from app.events.publisher import publisher
from app.main import app
from app.service.mode_service import (
    APPROVAL_KEY_PREFIX,
    APPROVAL_TTL_SECONDS,
    ApprovalLedger,
    AuthenticatedApprover,
    KEY_ACTIVE,
    KEY_GRAY,
    ModeApprovalRecord,
    ModeService,
    mode_service,
)
from common.config.settings import settings as platform_settings
from common.errors.codes import ErrorCode
from common.security.rbac import create_token
from app.service.mode_service import _InMemoryRedis

# 现场事实：mode router prefix="/complaints" + main.py 挂载 prefix="/api/v1"
_MODE_PATH = "/api/v1/complaints/mode"


class MemoryRedis:
    """T89 测试替身：set/get/delete/keys（TTL 过期判定由 Ledger 层兜底）。"""

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v, ex=None):
        self.data[k] = str(v)
        return True

    def delete(self, k):
        return self.data.pop(k, None) is not None

    def keys(self, pattern="*"):
        import fnmatch

        return [k for k in self.data if fnmatch.fnmatch(k, pattern)]


class SelectiveFailRedis(MemoryRedis):
    """审批记录键可写、模式键写入失败——模拟两阶段推进时 Redis 故障。"""

    def set(self, k, v, ex=None):
        if k.startswith(APPROVAL_KEY_PREFIX):
            return MemoryRedis.set(self, k, v, ex=ex)
        raise RuntimeError("redis down")


def _token(role: str) -> str:
    return create_token({"sub": str(uuid4()), "role": role})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(platform_settings, "ENABLE_AUTH", True)
    redis = MemoryRedis()
    set_redis_client(redis)
    publisher.clear()
    yield TestClient(app), redis
    set_redis_client(_InMemoryRedis())


class TestModeRouteAuth:
    def test_anonymous_rejected_401_no_state_change(self, client):
        c, redis = client
        resp = c.put(_MODE_PATH, json={"mode": "multi_agent", "signers": ["ops", "tech"]})
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"
        assert KEY_ACTIVE not in redis.data
        assert publisher.list_events("complaint.mode.switched") == []

    def test_low_privilege_role_403(self, client):
        c, _ = client
        resp = c.put(
            _MODE_PATH,
            headers={"Authorization": f"Bearer {_token('agent')}"},
            json={"mode": "multi_agent", "signers": ["ops", "tech"]},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "B0003"


class TestTwoPhaseApproval:
    def test_first_phase_registers_and_keeps_mode(self, client):
        c, redis = client
        resp = c.put(
            _MODE_PATH,
            headers={"Authorization": f"Bearer {_token('admin')}"},
            json={"mode": "multi_agent", "signers": ["ops", "tech"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["mode"] == "single_agent"
        assert KEY_ACTIVE not in redis.data
        assert publisher.list_events("complaint.mode.switched") == []

    def test_two_different_identities_advance(self, client):
        c, redis = client
        payload = {"mode": "multi_agent", "gray_percent": 50, "signers": ["ops", "tech"]}

        first = c.put(_MODE_PATH, headers={"Authorization": f"Bearer {_token('admin')}"}, json=payload)
        assert first.status_code == 200
        assert first.json()["code"] == 0
        assert first.json()["data"]["mode"] == "single_agent"

        second = c.put(
            _MODE_PATH, headers={"Authorization": f"Bearer {_token('supervisor')}"}, json=payload
        )
        assert second.status_code == 200
        assert second.json()["data"]["mode"] == "multi_agent"
        assert second.json()["data"]["gray_percent"] == 50
        assert redis.data.get(KEY_ACTIVE) == "multi_agent"
        assert redis.data.get(KEY_GRAY) == "50"
        evs = publisher.list_events("complaint.mode.switched")
        assert evs and evs[0].payload["to"] == "multi_agent"
        assert not [k for k in redis.data if k.startswith(APPROVAL_KEY_PREFIX)]

    def test_same_identity_twice_does_not_advance(self, client):
        c, redis = client
        token = _token("admin")
        payload = {"mode": "multi_agent", "gray_percent": 50, "signers": ["ops", "tech"]}
        first = c.put(_MODE_PATH, headers={"Authorization": f"Bearer {token}"}, json=payload)
        second = c.put(_MODE_PATH, headers={"Authorization": f"Bearer {token}"}, json=payload)
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["data"]["mode"] == "single_agent"
        assert KEY_ACTIVE not in redis.data

    def test_param_mismatch_rejects_old_record_and_reregisters(self, client):
        c, redis = client
        payload_a = {"mode": "multi_agent", "gray_percent": 50, "signers": ["ops", "tech"]}
        payload_b = {"mode": "multi_agent", "gray_percent": 100, "signers": ["ops", "tech"]}
        c.put(_MODE_PATH, headers={"Authorization": f"Bearer {_token('admin')}"}, json=payload_a)
        resp = c.put(
            _MODE_PATH, headers={"Authorization": f"Bearer {_token('supervisor')}"}, json=payload_b
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["mode"] == "single_agent"
        assert KEY_ACTIVE not in redis.data
        pending_keys = [k for k in redis.data if k.startswith(APPROVAL_KEY_PREFIX)]
        assert len(pending_keys) == 1
        record = json.loads(redis.data[pending_keys[0]])
        assert record["target_gray_percent"] == 100

    def test_approval_record_traceable_with_ttl(self, client):
        c, redis = client
        c.put(
            _MODE_PATH,
            headers={"Authorization": f"Bearer {_token('admin')}"},
            json={"mode": "multi_agent", "signers": ["ops", "tech"]},
        )
        pending_keys = [k for k in redis.data if k.startswith(APPROVAL_KEY_PREFIX)]
        assert len(pending_keys) == 1
        record = json.loads(redis.data[pending_keys[0]])
        assert record["approver_role"] == "admin"
        assert record["target_mode"] == "multi_agent"
        assert record["target_gray_percent"] == 100
        assert APPROVAL_TTL_SECONDS == 600

    def test_invalid_mode_a5014_first(self, client):
        c, redis = client
        resp = c.put(
            _MODE_PATH,
            headers={"Authorization": f"Bearer {_token('admin')}"},
            json={"mode": "invalid_mode", "signers": ["ops", "tech"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "A5014"
        assert KEY_ACTIVE not in redis.data

    def test_advance_redis_failure_rolls_back_a5014(self, client):
        """第二阶段推进时 Redis 故障 → A5014、模式键不写入（回滚语义）。"""
        c, redis = client
        redis = SelectiveFailRedis()
        set_redis_client(redis)
        payload = {"mode": "multi_agent", "gray_percent": 50, "signers": ["ops", "tech"]}
        first = c.put(_MODE_PATH, headers={"Authorization": f"Bearer {_token('admin')}"}, json=payload)
        assert first.json()["code"] == 0
        second = c.put(
            _MODE_PATH, headers={"Authorization": f"Bearer {_token('supervisor')}"}, json=payload
        )
        body = second.json()
        assert body["code"] == "A5014"
        assert body["message"] == "模式切换失败，已回滚"
        assert KEY_ACTIVE not in redis.data


class TestServiceLevelLedger:
    def test_ledger_expired_record_ignored(self):
        redis = MemoryRedis()
        ledger = ApprovalLedger(lambda: redis)
        ledger.register(
            ModeApprovalRecord(
                approver=AuthenticatedApprover(sub="u1", role="admin"),
                target_mode="multi_agent",
                target_gray_percent=50,
                recorded_at=0.0,
                ttl_seconds=600,
            )
        )
        assert ledger.find_pending() is None

    def test_ledger_same_identity_not_other(self):
        redis = MemoryRedis()
        ledger = ApprovalLedger(lambda: redis)
        approver = AuthenticatedApprover(sub="u1", role="admin")
        ledger.register(
            ModeApprovalRecord(
                approver=approver,
                target_mode="multi_agent",
                target_gray_percent=None,
                recorded_at=time.time(),
                ttl_seconds=600,
            )
        )
        pending = ledger.find_pending()
        assert pending is not None
        assert not ledger.is_other_identity(pending, approver)
        assert ledger.is_other_identity(pending, AuthenticatedApprover(sub="u2", role="admin"))

    def test_full_switch_registers_and_advances(self):
        class C:
            def get_ai_human_rate(self):
                return 0.9

            def get_cross_rate(self):
                return 0.85

        m = ModeService(consistency_provider=C())
        assert m.full_switch_if_consistent() is True
        assert m.get_active() == "multi_agent"
        assert m.get_gray_percent() == 100
        assert m._ledger.find_pending() is None

    def test_full_switch_not_ready_no_change(self):
        m = ModeService()
        assert m.full_switch_if_consistent() is False
        assert m.get_active() == "single_agent"

    def test_switch_signature_has_no_signers(self):
        """T89 契约回归：switch 签名不再含 signers，改用 approver。"""
        import inspect

        sig = inspect.signature(ModeService.switch)
        assert "signers" not in sig.parameters
        assert "approver" in sig.parameters

    def test_service_singleton_ledger_bound(self):
        """mode_service 单例的 Ledger 沿用 _redis 装配路径（set_redis_client 动态替换兼容）。"""
        fresh = _InMemoryRedis()
        set_redis_client(fresh)
        try:
            assert mode_service._ledger._redis is fresh
        finally:
            set_redis_client(_InMemoryRedis())