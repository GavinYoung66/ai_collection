"""T58 / 5.3：qc-service lifespan shutdown 接入 engine.dispose 单测。

验收判据：shutdown 清单内 ``db_engine → await engine.dispose()`` 在 lifespan
关停路径被调用恰好一次（monkeypatch 计数，守护测试防回退网 11.2）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import reset_dp_client, reset_redis as reset_qc_redis
from app.main import app
from common.config.settings import settings as platform_settings
from common.llm.reliability import set_default_service
from common.resilience import hooks
from common.resilience._redis import reset_redis as reset_common_redis


class _FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    async def dispose(self):
        self.dispose_calls += 1


def test_lifespan_shutdown_disposes_engine_once(monkeypatch):
    """lifespan 关停：engine.dispose 被调用恰好一次（T58 清单登记项）。

    本地跑测试无 CI 注入的密钥环境变量，须显式注入合法值通过 fail-fast（T48）。
    """
    import common.db.session as db_session

    monkeypatch.setattr(
        platform_settings, "JWT_SECRET", "ci_jwt_secret_for_tests_only"
    )
    monkeypatch.setattr(platform_settings, "AES_KEY", "a" * 64)
    fake = _FakeEngine()
    monkeypatch.setattr(db_session, "engine", fake)

    try:
        with TestClient(app):
            assert fake.dispose_calls == 0, "运行期不应 dispose"
        assert fake.dispose_calls == 1, "关停路径应恰好 dispose 一次"
    finally:
        hooks.set_publisher(None)
        set_default_service(None)
        reset_common_redis()
        reset_qc_redis()
        reset_dp_client()