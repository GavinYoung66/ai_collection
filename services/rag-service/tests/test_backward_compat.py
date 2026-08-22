"""向后兼容单元测试（§16.11）。

开关全关闭时 hybrid_search 不调用 OCR/ASR/LLM/Routing/Weighting/RowAccess/Session 各服务。
用 MagicMock spy 断言每个 V2 服务未被实例化或未被调用。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.search_service import SearchService


def _build_search_service_with_spies():
    """构造 SearchService，注入所有 V2 服务均为 spy MagicMock。"""
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

    # V2 服务 spies
    rewrite_svc = MagicMock()
    rewrite_svc.rewrite = AsyncMock()
    routing_svc = MagicMock()
    routing_svc.classify_and_weight = MagicMock(return_value=MagicMock(
        intent_tag=None, weights={}, degraded=True
    ))
    weighting_svc = MagicMock()
    weighting_svc.apply = MagicMock(side_effect=lambda items, meta: items)
    row_access_svc = MagicMock()
    row_access_svc.filter = MagicMock(side_effect=lambda items, *a, **k: items)
    session_svc = MagicMock()
    session_svc.get_recent_queries = AsyncMock(return_value=[])
    session_svc.record = AsyncMock()

    svc = SearchService(
        milvus_client=mock_milvus,
        es_client=mock_es,
        embed_client=mock_embed,
        rerank_client=mock_rerank,
        log_service=mock_log,
        rewrite_service=rewrite_svc,
        routing_service=routing_svc,
        weighting_service=weighting_svc,
        row_access_service=row_access_svc,
        session_service=session_svc,
    )
    spies = {
        "rewrite": rewrite_svc,
        "routing": routing_svc,
        "weighting": weighting_svc,
        "row_access": row_access_svc,
        "session": session_svc,
    }
    return svc, spies


@pytest.fixture
def all_switches_off(monkeypatch):
    """所有 7 个 V2 开关关闭（=V1 行为）。"""
    from common.config.settings import settings
    monkeypatch.setattr(settings, "KB_QUERY_REWRITE_ENABLED", False)
    monkeypatch.setattr(settings, "KB_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "KB_WEIGHTING_ENABLED", False)
    monkeypatch.setattr(settings, "KB_ROW_LEVEL_ENABLED", False)
    monkeypatch.setattr(settings, "KB_MULTITURN_ENABLED", False)
    monkeypatch.setattr(settings, "KB_EMBED_DEGRADED_KEEP_TEXT", False)
    monkeypatch.setattr(settings, "ES_DUAL_WRITE", False)


class TestBackwardCompatSwitchesOff:
    @pytest.mark.asyncio
    async def test_rewrite_not_called(self, all_switches_off):
        """开关全关 → RewriteService.rewrite 未被调用。"""
        svc, spies = _build_search_service_with_spies()
        await svc.hybrid_search("测试", "regulation", 5, rewrite=True, session_id="s1")
        spies["rewrite"].rewrite.assert_not_called()

    @pytest.mark.asyncio
    async def test_routing_not_called(self, all_switches_off):
        """开关全关 → RoutingService.classify_and_weight 未被调用。"""
        svc, spies = _build_search_service_with_spies()
        await svc.hybrid_search("协商还款", "regulation", 5)
        spies["routing"].classify_and_weight.assert_not_called()

    @pytest.mark.asyncio
    async def test_weighting_not_called(self, all_switches_off):
        """开关全关 → WeightingService.apply 未被调用。"""
        svc, spies = _build_search_service_with_spies()
        await svc.hybrid_search("测试", "regulation", 5)
        spies["weighting"].apply.assert_not_called()

    @pytest.mark.asyncio
    async def test_row_access_not_called(self, all_switches_off):
        """开关全关 → RowAccessService.filter 未被调用。"""
        svc, spies = _build_search_service_with_spies()
        jwt = {"role": "AGENT", "department": "催收一部"}
        await svc.hybrid_search("测试", "regulation", 5, jwt_payload=jwt)
        spies["row_access"].filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_get_not_called(self, all_switches_off):
        """开关全关 → SessionService.get_recent_queries 未被调用。"""
        svc, spies = _build_search_service_with_spies()
        await svc.hybrid_search("测试", "regulation", 5, session_id="s1", rewrite=True)
        spies["session"].get_recent_queries.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_record_not_called(self, all_switches_off):
        """开关全关 → SessionService.record 未被调用。"""
        svc, spies = _build_search_service_with_spies()
        await svc.hybrid_search("测试", "regulation", 5, session_id="s1")
        spies["session"].record.assert_not_called()

    @pytest.mark.asyncio
    async def test_v1_behavior_search_returns_results(self, all_switches_off):
        """开关全关 → 检索正常返回结果（V1 行为不变）。"""
        svc, _ = _build_search_service_with_spies()
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert "results" in result
        assert "degraded" in result
        assert "trace_id" in result
        assert len(result["results"]) >= 1

    @pytest.mark.asyncio
    async def test_dedup_always_active_even_switches_off(self, all_switches_off):
        """同 doc_id 去重强制无开关，开关全关时仍生效。"""
        svc, _ = _build_search_service_with_spies()
        # mock_milvus 返回同 doc 两个 chunk
        svc.milvus.search = AsyncMock(return_value=[
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.9},
            {"chunk_id": "c2", "doc_id": "d1", "category": "regulation", "score": 0.8},
        ])
        svc.es.search = AsyncMock(return_value=[
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "content": "x", "score": 5.0},
        ])
        result = await svc.hybrid_search("测试", "regulation", 5)
        doc_ids = [r["doc_id"] for r in result["results"]]
        # 同 doc_id 去重后唯一
        assert len(set(doc_ids)) == len(doc_ids)

    @pytest.mark.asyncio
    async def test_no_ocr_asr_called_in_search_path(self, all_switches_off):
        """检索路径不触发 OCR/ASR（它们仅在文档入库时使用）。"""
        svc, spies = _build_search_service_with_spies()
        await svc.hybrid_search("测试", "regulation", 5)
        # SearchService 不持有 OCR/ASR 引用，这里验证不抛异常即通过
        assert not hasattr(svc, "_ocr_client")
        assert not hasattr(svc, "_asr_client")

    @pytest.mark.asyncio
    async def test_cache_key_v2_prefix_isolated(self, all_switches_off, monkeypatch):
        """V2 缓存 key 前缀隔离：开关全关时仍使用 v2 前缀（含 flags_hash）。"""
        from app.services import query_cache
        key = query_cache.make_cache_key("测试", "regulation", 5)
        assert key.startswith("rag:cache:query:v2:")
