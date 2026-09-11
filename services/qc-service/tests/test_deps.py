"""QC-1.3：deps 注入 Redis DB 3、settings、规则快照；复用 common session。"""
from app import deps
from app.core.config import qc_settings
from common.db.session import get_session as common_get_session


class TestDeps:
    def test_get_session_is_common(self):
        assert deps.get_session is common_get_session

    def test_get_settings_returns_qc_settings(self):
        assert deps.get_settings() is qc_settings

    def test_redis_uses_db_3(self):
        client = deps.build_redis()
        assert client.connection_pool.connection_kwargs["db"] == 3

    async def test_get_redis_db_3(self):
        deps.reset_redis()
        client = await deps.get_redis()
        try:
            assert client.connection_pool.connection_kwargs["db"] == 3
        finally:
            deps.reset_redis()
            await client.aclose()

    def test_active_qc_rules_snapshot(self):
        snap = deps.get_active_qc_rules()
        assert snap["version"] == "v1.0"
        assert snap["status"] == "active"
        assert isinstance(snap["rules"], list)
        assert any(r.get("rule_id") == "R14" for r in snap["rules"])
