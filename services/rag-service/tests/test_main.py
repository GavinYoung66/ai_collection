"""main.py 生命周期（T37：lifespan）、幂等关闭与启动韧性接线测试。"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import main
from app.services import rate_limit as rate_limit_mod
from app.settings import settings
from common.llm.reliability import resolve_service_name, set_default_service
from common.resilience import hooks
from common.resilience._redis import get_redis as common_get_redis
from common.resilience._redis import reset_redis as reset_common_redis
from common.resilience.breaker import make_llm_breaker_key, make_mcp_breaker_key


@pytest.fixture()
def fake_startup_env(monkeypatch):
    """屏蔽 startup 的外部依赖：signal、Milvus/ES、DB/Redis 派生初始化与观测配置。

    不走 TestClient：lifespan/startup 里 signal.signal 仅主线程可用，
    TestClient 的工作线程会触发 ValueError。
    """
    monkeypatch.setattr(main.signal, "signal", lambda *_a, **_k: None)

    class _Ok:
        async def ensure_collection(self):
            return None

        async def ensure_index(self):
            return None

    fake = _Ok()
    monkeypatch.setattr("app.api.deps.get_milvus_client", lambda: fake)
    monkeypatch.setattr("app.api.deps.get_es_client", lambda: fake)

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(main, "_ensure_sensitive_words_baseline", _noop)
    monkeypatch.setattr(main, "_resume_backfill_pending", _noop)
    monkeypatch.setattr(main, "_sweep_stale_ingest_jobs", _noop)
    monkeypatch.setattr(
        "app.services.search_trace.configure_langsmith_env", lambda: None
    )
    monkeypatch.setattr(
        "app.services.search_trace.configure_langfuse_env", lambda: None
    )
    return fake


@pytest.fixture()
def unwire_env():
    """测试后清理全局接线，避免套件间泄漏。"""
    yield
    hooks.set_publisher(None)
    set_default_service(None)
    reset_common_redis()
    rate_limit_mod._redis = None
    rate_limit_mod._injected = None


class TestShutdownClosesClients:
    @pytest.mark.asyncio
    async def test_shutdown_closes_clients(self):
        """shutdown 依次幂等关闭 es/embed/rerank/milvus/redis 全局客户端。"""
        es = AsyncMock()
        embed = AsyncMock()
        rerank = AsyncMock()
        milvus = AsyncMock()
        redis = AsyncMock()
        with patch("app.api.deps._es_client", es), \
             patch("app.api.deps._embed_client", embed), \
             patch("app.api.deps._rerank_client", rerank), \
             patch("app.api.deps._milvus_client", milvus), \
             patch("app.api.deps._redis_client", redis):
            await main.shutdown()
        es.close.assert_awaited_once()
        embed.close.assert_awaited_once()
        rerank.close.assert_awaited_once()
        milvus.close.assert_awaited_once()
        redis.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_all_none_no_error(self):
        """全局客户端均为 None（未初始化）→ 幂等跳过，不抛异常。"""
        with patch("app.api.deps._es_client", None), \
             patch("app.api.deps._embed_client", None), \
             patch("app.api.deps._rerank_client", None), \
             patch("app.api.deps._milvus_client", None), \
             patch("app.api.deps._redis_client", None):
            await main.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_single_close_failure_isolated(self):
        """单个客户端 close 失败仅 warning，其余客户端仍被关闭。"""
        es = AsyncMock()
        es.close = AsyncMock(side_effect=RuntimeError("es down"))
        embed = AsyncMock()
        milvus = AsyncMock()
        with patch("app.api.deps._es_client", es), \
             patch("app.api.deps._embed_client", embed), \
             patch("app.api.deps._rerank_client", None), \
             patch("app.api.deps._milvus_client", milvus), \
             patch("app.api.deps._redis_client", None):
            await main.shutdown()
        embed.close.assert_awaited_once()
        milvus.close.assert_awaited_once()


class TestStartupResilienceWiring:
    @pytest.mark.asyncio
    async def test_startup_sets_rag_prefix_and_unwires_hooks(
        self, fake_startup_env, unwire_env
    ):
        """startup 注入 rag 前缀 / Redis / publisher；shutdown 后 publisher 为 None。"""
        await main.startup()
        assert hooks.get_publisher() is not None
        assert resolve_service_name() == "rag"
        assert make_llm_breaker_key("rag", "qwen-plus") == "rag:llm_degraded:qwen-plus"
        assert make_mcp_breaker_key("rag", "embed") == "rag:mcp_degraded:embed"
        assert make_llm_breaker_key(resolve_service_name(), "qwen-plus").startswith(
            "rag:"
        )
        redis = common_get_redis()
        assert redis.connection_pool.connection_kwargs.get("db") == settings.RAG_REDIS_DB
        await main.shutdown()
        assert hooks.get_publisher() is None


class TestLifespan:
    """T37：生命周期统一走 lifespan（@app.on_event 已移除）。"""

    @pytest.mark.asyncio
    async def test_lifespan_runs_startup_then_shutdown(
        self, fake_startup_env, unwire_env
    ):
        """进入 lifespan 执行 startup 接线，退出后执行 shutdown 清理。"""
        async with main.lifespan(main.app):
            assert hooks.get_publisher() is not None
            assert resolve_service_name() == "rag"
        assert hooks.get_publisher() is None

    @pytest.mark.asyncio
    async def test_lifespan_closes_clients_on_exit(
        self, fake_startup_env, unwire_env
    ):
        """lifespan 退出路径触发全局客户端幂等关闭。"""
        es = AsyncMock()
        redis = AsyncMock()
        with patch("app.api.deps._es_client", es), \
             patch("app.api.deps._embed_client", None), \
             patch("app.api.deps._rerank_client", None), \
             patch("app.api.deps._milvus_client", None), \
             patch("app.api.deps._redis_client", redis):
            async with main.lifespan(main.app):
                pass
        es.close.assert_awaited_once()
        redis.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_registered_on_app(self):
        """FastAPI 实例绑定 lifespan 入口，且应用源码不再注册 on_event（T37 验收）。"""
        assert main.app.router.lifespan_context is main.lifespan

        app_dir = Path(main.__file__).parent
        for py_file in app_dir.rglob("*.py"):
            assert ".on_event(" not in py_file.read_text(encoding="utf-8"), (
                f"{py_file} 仍注册 @app.on_event 生命周期钩子，应迁移到 lifespan"
            )
