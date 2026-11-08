"""T112：complaint mode_service 生产恒为 InMemoryRedis → lifespan 注入共享 Redis。

spec complaint-service/mode「模式状态多副本一致」：模式状态 MUST 存储于跨副本共享
的存储（Redis）并在服务 lifespan 显式装配，MUST NOT 依赖进程内默认实现，导致
多副本状态不一致或重启后丢失。

现场核验（2026-11-07）：报告所称「app 内无 set_redis_client 装配点」与现场不一致——
app/api/routes/mode.py 已有 set_redis_client（T89 实施时新增的测试辅助，仅测试
使用）；但生产装配缺口成立：lifespan 此前无任何注入，mode_service 单例默认
_InMemoryRedis，多副本不一致、重启即丢。本整改在 ModeService 增设正式装配点
set_redis_client（routes 层委托），lifespan 探活后注入共享同步 Redis；失败降级
保留内存实现并置 redis_degraded（/health 暴露）。

V7/V11 解耦留痕（design D6/D7）：「lifespan 注入共享 Redis」为已裁决确定动作，
不等 V7（dp _pending_retries 多副本易失）/V11（多副本 create_session 双行）取证
结论；多副本集成验证登记为运维冒烟项（V7/V11 输入到位后补测），本测试覆盖
单副本语义。
"""
from __future__ import annotations

import fnmatch

from fastapi.testclient import TestClient

from app.api.routes.mode import set_redis_client
from app.events.publisher import publisher
from app.main import app
from app.service.mode_service import (
    KEY_ACTIVE,
    AuthenticatedApprover,
    ModeService,
    _InMemoryRedis,
    build_shared_redis_client,
    mode_service,
)


class _FakeSharedRedis:
    """同步接口共享 Redis 替身（形态对齐 redis.Redis：get/set/delete/keys/ping/close）。"""

    def __init__(self, ping_ok: bool = True):
        self.data: dict[str, str] = {}
        self._ping_ok = ping_ok
        self.closed = False

    def ping(self):
        if not self._ping_ok:
            raise RuntimeError("redis down")
        return True

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v, ex=None):
        self.data[k] = str(v)
        return True

    def delete(self, k):
        return self.data.pop(k, None) is not None

    def keys(self, pattern: str = "*"):
        return [k for k in self.data if fnmatch.fnmatch(k, pattern)]

    def close(self):
        self.closed = True


def _approver(sub: str, role: str) -> AuthenticatedApprover:
    return AuthenticatedApprover(sub=sub, role=role)


class TestBuildSharedRedisClient:
    def test_connection_params_single_source_common_settings(self):
        """连接参数单源 common settings，decode_responses 与 _InMemoryRedis 值语义对齐。"""
        from common.config.settings import settings as platform_settings

        client = build_shared_redis_client()
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["host"] == platform_settings.REDIS_HOST
        assert kwargs["port"] == platform_settings.REDIS_PORT
        assert kwargs["db"] == platform_settings.REDIS_DB
        assert kwargs["decode_responses"] is True
        # 短 socket 超时（与 common.resilience 同形态）
        assert kwargs["socket_timeout"] == 1.0
        assert kwargs["socket_connect_timeout"] == 1.0


class TestLifespanAssembly:
    def test_lifespan_assembles_shared_redis_into_mode_service(self, monkeypatch):
        """装配成功：mode 状态与 ApprovalLedger 共享同一 client（单一注入点）。"""
        fake = _FakeSharedRedis()
        monkeypatch.setattr(
            "app.service.mode_service.build_shared_redis_client", lambda: fake
        )
        try:
            with TestClient(app) as client:
                assert mode_service._redis is fake
                assert mode_service._ledger._redis is fake
                assert client.app.state.mode_redis is fake
                assert client.app.state.redis_degraded is False
                assert client.get("/health").json()["redis"] == "ok"
            assert fake.closed is True  # shutdown 关闭共享连接
        finally:
            set_redis_client(_InMemoryRedis())
            publisher.clear()

    def test_lifespan_ping_failure_degrades_to_memory(self, monkeypatch):
        """探活失败：降级保留进程内实现（不阻断启动），redis_degraded 由 health 暴露。"""
        fake = _FakeSharedRedis(ping_ok=False)
        monkeypatch.setattr(
            "app.service.mode_service.build_shared_redis_client", lambda: fake
        )
        try:
            with TestClient(app) as client:
                assert isinstance(mode_service._redis, _InMemoryRedis)
                assert client.app.state.mode_redis is None
                assert client.app.state.redis_degraded is True
                assert client.get("/health").json()["redis"] == "degraded"
                # 降级下模式管理语义仍可用（内存单副本）：两阶段推进走通
                ok, err = mode_service.switch(
                    "multi_agent", approver=_approver("a", "admin")
                )
                assert (ok, err) == (True, None)
                ok, err = mode_service.switch(
                    "multi_agent", approver=_approver("b", "supervisor")
                )
                assert ok is True
                assert mode_service.get_active() == "multi_agent"
            # T112：ping 失败后客户端必须关闭，避免启动期连接池泄漏
            assert fake.closed is True
        finally:
            set_redis_client(_InMemoryRedis())
            publisher.clear()


class TestServiceAssemblyPoint:
    def test_set_redis_client_binds_ledger_single_injection_point(self):
        """单一注入点：service 层装配后 ApprovalLedger（T89 形态）同步跟随。"""
        fresh = _FakeSharedRedis()
        mode_service.set_redis_client(fresh)
        try:
            assert mode_service._ledger._redis is fresh
        finally:
            set_redis_client(_InMemoryRedis())


class TestSharedStateSemantics:
    """单副本语义形态验证（spec 两场景）；多副本集成登记为运维冒烟项（V7/V11）。"""

    def test_second_instance_reads_same_mode(self):
        """两副本读到同一模式：实例 A 切换后实例 B 经同一共享 client 读到一致值。"""
        shared = _FakeSharedRedis()
        a = ModeService()
        a.set_redis_client(shared)
        b = ModeService()
        b.set_redis_client(shared)

        ok, err = a.switch("multi_agent", approver=_approver("a", "admin"))
        assert (ok, err) == (True, None)  # 第一阶段仅登记审批
        ok, err = b.switch("multi_agent", approver=_approver("b", "supervisor"))
        assert ok is True  # 第二不同身份推进切换

        assert b.get_active() == "multi_agent"
        assert a.get_active() == "multi_agent"
        assert shared.data.get(KEY_ACTIVE) == "multi_agent"
        publisher.clear()

    def test_mode_survives_remount_after_restart(self):
        """重启后模式保持：新实例重新挂载同一共享存储，不回退进程默认值。"""
        shared = _FakeSharedRedis()
        first = ModeService()
        first.set_redis_client(shared)
        ok, _ = first.switch("multi_agent", approver=_approver("a", "admin"))
        assert ok is True
        ok, _ = first.switch("multi_agent", approver=_approver("b", "supervisor"))
        assert ok is True
        assert first.get_active() == "multi_agent"

        restarted = ModeService()  # 进程重启：默认仍是内存实现
        restarted.set_redis_client(shared)  # lifespan 重新装配共享存储
        assert restarted.get_active() == "multi_agent"
        publisher.clear()