"""main.py shutdown 幂等关闭测试（rag-service-final-fix 5.15/5.16）。"""
from unittest.mock import AsyncMock, patch

import pytest

from app import main


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
