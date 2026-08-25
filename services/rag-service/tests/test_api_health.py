"""健康检查探活测试（rag-service-final-fix 3.4：A 类 /health 依赖探活）。"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.health import _check_database, _check_embedding, probe_health


class TestHealthProbe:
    @pytest.mark.asyncio
    async def test_health_all_dependencies_up(self, monkeypatch):
        """四项依赖全部正常 → healthy + checks 全 ok + 契约字段完整。"""
        monkeypatch.setattr("app.api.health._check_database", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_milvus", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_redis", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_embedding", AsyncMock(return_value="ok"))
        body = await probe_health()
        assert body["status"] == "healthy"
        assert body["service"] == "rag-service"
        assert body["version"] == "1.0.0"
        assert body["checks"] == {
            "database": "ok", "milvus": "ok", "redis": "ok", "embedding": "ok",
        }
        assert isinstance(body["ts"], int)

    @pytest.mark.asyncio
    async def test_health_single_dependency_down_unhealthy(self, monkeypatch):
        """单个依赖失败 → unhealthy（标记而非抛错）。"""
        monkeypatch.setattr("app.api.health._check_database", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_milvus", AsyncMock(return_value="fail"))
        monkeypatch.setattr("app.api.health._check_redis", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_embedding", AsyncMock(return_value="ok"))
        body = await probe_health()
        assert body["status"] == "unhealthy"
        assert body["checks"]["milvus"] == "fail"

    @pytest.mark.asyncio
    async def test_health_probe_timeout_not_500(self, monkeypatch):
        """数据库探活超时 → 探活函数返回 fail 不抛异常（HTTP 层不产生 500）。"""
        import asyncio

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, stmt):
                await asyncio.sleep(0.5)
                return None

        monkeypatch.setattr("app.api.health.async_session_factory", lambda: FakeSession())
        monkeypatch.setattr("app.api.health.PROBE_TIMEOUT", 0.05)
        assert await _check_database() == "fail"

    @pytest.mark.asyncio
    async def test_health_embedding_connection_error_returns_fail(self):
        """embedding 服务不可达 → 探活函数返回 fail，不抛异常（HTTP 层不产生 500）。"""
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get = AsyncMock(side_effect=httpx.ConnectError("conn refused"))
        with patch("app.api.health.httpx.AsyncClient", return_value=mock_ctx):
            assert await _check_embedding() == "fail"

    @pytest.mark.asyncio
    async def test_health_degraded_mode_dependency_down_not_unhealthy(self, monkeypatch):
        """降级模式（KB_HEALTH_DEGRADED_MODE=true）：依赖失败只告警 → status=degraded 而非 unhealthy，
        checks 保留真实状态（HTTP 200，不触发容器重启）。"""
        from common.config.settings import settings as global_settings

        monkeypatch.setattr(global_settings, "KB_HEALTH_DEGRADED_MODE", True)
        monkeypatch.setattr("app.api.health._check_database", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_milvus", AsyncMock(return_value="fail"))
        monkeypatch.setattr("app.api.health._check_redis", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_embedding", AsyncMock(return_value="ok"))
        body = await probe_health()
        assert body["status"] == "degraded"
        assert body["checks"]["milvus"] == "fail"
        assert body["checks"]["database"] == "ok"

    @pytest.mark.asyncio
    async def test_health_degraded_mode_all_up_still_healthy(self, monkeypatch):
        """降级模式下四项依赖全部正常 → 仍为 healthy（降级开关不影响正常路径）。"""
        from common.config.settings import settings as global_settings

        monkeypatch.setattr(global_settings, "KB_HEALTH_DEGRADED_MODE", True)
        monkeypatch.setattr("app.api.health._check_database", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_milvus", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_redis", AsyncMock(return_value="ok"))
        monkeypatch.setattr("app.api.health._check_embedding", AsyncMock(return_value="ok"))
        body = await probe_health()
        assert body["status"] == "healthy"
        assert all(v == "ok" for v in body["checks"].values())


class TestHealthEndpoint:
    def test_root_and_api_v1_health_share_probe(self):
        """根路径 /health 与 /api/v1/health 共用同一探活实现（统一响应结构）。"""
        from fastapi.testclient import TestClient

        from app.main import app

        probe_result = {
            "status": "healthy",
            "service": "rag-service",
            "version": "1.0.0",
            "checks": {
                "database": "ok", "milvus": "ok", "redis": "ok", "embedding": "ok",
            },
            "ts": 1724054400,
        }
        client = TestClient(app)
        with (
            patch("app.api.health.probe_health", return_value=probe_result),
            patch("app.main.probe_health", return_value=probe_result),
        ):
            root = client.get("/health")
            api = client.get("/api/v1/health")
        assert root.status_code == 200
        assert api.status_code == 200
        assert root.json() == probe_result
        assert api.json() == probe_result
