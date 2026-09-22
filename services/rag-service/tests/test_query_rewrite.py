"""Query 改写服务单元测试（§7.7 / §16.3）。

覆盖：
- 成功改写（别字→正确词）
- 低置信度降级（confidence < 0.6）
- 熔断 bypass
- JSON 非法 fallback
- 开关全关时 SearchService 不调用（spy）
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.query_rewrite_service import RewriteService


def _mock_response(text: str, status_code: int = 200) -> httpx.Response:
    """构造 DashScope 风格的成功响应。"""
    return httpx.Response(
        status_code=status_code,
        json={"output": {"text": text}},
        request=httpx.Request("POST", "https://example.com"),
    )


@pytest.fixture
def fake_redis():
    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.setex = AsyncMock()
    return redis


@pytest.fixture
def api_key(monkeypatch):
    """注入 DASHSCOPE_API_KEY。"""
    from app.settings import settings
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "sk-test-key")


class TestRewriteSuccess:
    @pytest.mark.asyncio
    async def test_successful_rewrite(self, fake_redis, api_key):
        """LLM 返回高置信度改写 → degraded=False。"""
        llm_output = json.dumps(
            {"rewritten_query": "逾期协商还款方案", "confidence": 0.92}
        )
        svc = RewriteService(redis=fake_redis)
        with patch.object(svc, "_get_client", new=AsyncMock(
            return_value=MagicMock(
                post=AsyncMock(return_value=_mock_response(llm_output))
            )
        )):
            result = await svc.rewrite("逾期协商还宽方案")
        assert result.degraded is False
        assert result.rewritten_query == "逾期协商还款方案"
        assert result.confidence == 0.92


class TestRewriteDegraded:
    @pytest.mark.asyncio
    async def test_low_confidence_degraded(self, fake_redis, api_key):
        """confidence < 0.6 → degraded=True，rewritten_query 退回原 query。"""
        llm_output = json.dumps(
            {"rewritten_query": "猜测的改写", "confidence": 0.3}
        )
        svc = RewriteService(redis=fake_redis)
        with patch.object(svc, "_get_client", new=AsyncMock(
            return_value=MagicMock(
                post=AsyncMock(return_value=_mock_response(llm_output))
            )
        )):
            result = await svc.rewrite("原查询")
        assert result.degraded is True
        assert result.rewritten_query == "原查询"

    @pytest.mark.asyncio
    async def test_circuit_break_bypass(self, fake_redis, api_key):
        """进程内熔断 open → 直接降级，不调 LLM，不写 rag:rewrite:degraded。"""
        svc = RewriteService(redis=fake_redis)
        for _ in range(3):
            await svc._breaker.record_failure(RuntimeError("rewrite failed"))
        with patch.object(
            svc,
            "_get_client",
            new=AsyncMock(side_effect=AssertionError("must not call LLM")),
        ):
            result = await svc.rewrite("测试查询")
        assert result.degraded is True
        assert result.rewritten_query == "测试查询"
        assert result.confidence == 0.0
        fake_redis.setex.assert_not_called()
        fake_redis.exists.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_json_fallback(self, fake_redis, api_key):
        """LLM 返回非 JSON → degraded=True，退回原 query。"""
        svc = RewriteService(redis=fake_redis)
        with patch.object(svc, "_get_client", new=AsyncMock(
            return_value=MagicMock(
                post=AsyncMock(return_value=_mock_response("这不是JSON"))
            )
        )):
            result = await svc.rewrite("原查询")
        assert result.degraded is True
        assert result.rewritten_query == "原查询"

    @pytest.mark.asyncio
    async def test_no_api_key_degraded(self, fake_redis, monkeypatch):
        """未配置 DASHSCOPE_API_KEY → 直接降级。"""
        from app.settings import settings
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
        svc = RewriteService(redis=fake_redis)
        result = await svc.rewrite("测试")
        assert result.degraded is True
        assert result.rewritten_query == "测试"

    @pytest.mark.asyncio
    async def test_json_code_block_parsed(self, fake_redis, api_key):
        """LLM 输出被 ```json 代码块包裹 → 仍能正确解析。"""
        llm_output = "```json\n" + json.dumps(
            {"rewritten_query": "归一化查询", "confidence": 0.85}
        ) + "\n```"
        svc = RewriteService(redis=fake_redis)
        with patch.object(svc, "_get_client", new=AsyncMock(
            return_value=MagicMock(
                post=AsyncMock(return_value=_mock_response(llm_output))
            )
        )):
            result = await svc.rewrite("原查询")
        assert result.degraded is False
        assert result.rewritten_query == "归一化查询"


class TestRewriteSwitchBypass:
    @pytest.mark.asyncio
    async def test_search_not_call_rewrite_when_switch_off(self, monkeypatch):
        """KB_QUERY_REWRITE_ENABLED=False → SearchService 不调用 RewriteService.rewrite。"""
        from app.settings import settings
        monkeypatch.setattr(settings, "KB_QUERY_REWRITE_ENABLED", False)

        from app.services.search_service import SearchService

        mock_milvus = MagicMock()
        mock_milvus.search = AsyncMock(return_value=[
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.9},
        ])
        mock_es = MagicMock()
        mock_es.search = AsyncMock(return_value=[
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "content": "x", "score": 5.0},
        ])
        mock_embed = MagicMock()
        mock_embed.embed = AsyncMock(return_value=[0.1] * 1024)
        mock_rerank = MagicMock()
        mock_rerank.is_degraded = AsyncMock(return_value=False)
        mock_rerank.rerank = AsyncMock(return_value=[
            {"chunk_id": "c1", "rerank_score": 0.9}
        ])
        mock_log = MagicMock()
        mock_log.log = AsyncMock()

        rewrite_svc = MagicMock()
        rewrite_svc.rewrite = AsyncMock()

        svc = SearchService(
            milvus_client=mock_milvus,
            es_client=mock_es,
            embed_client=mock_embed,
            rerank_client=mock_rerank,
            log_service=mock_log,
            rewrite_service=rewrite_svc,
        )
        await svc.hybrid_search("测试", "regulation", 5, rewrite=True)
        rewrite_svc.rewrite.assert_not_called()
