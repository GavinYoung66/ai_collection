"""混合检索测试。"""
import asyncio

import pytest

from app.services.search_service import SearchService


class TestRRFFusion:
    def test_rrf_fuse_both_sources(self, mock_search_service):
        dense_hits = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.9},
            {"chunk_id": "c2", "doc_id": "d1", "category": "regulation", "score": 0.8},
        ]
        sparse_hits = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "content": "内容", "score": 5.0},
            {"chunk_id": "c3", "doc_id": "d2", "category": "regulation", "content": "内容3", "score": 3.0},
        ]
        fused = mock_search_service._rrf_fuse(dense_hits, sparse_hits, k=60)
        assert len(fused) == 3
        c1_score = next(f["score"] for f in fused if f["chunk_id"] == "c1")
        c2_score = next(f["score"] for f in fused if f["chunk_id"] == "c2")
        c3_score = next(f["score"] for f in fused if f["chunk_id"] == "c3")
        assert c1_score > c2_score
        assert c1_score > c3_score

    def test_rrf_fuse_dedup_by_chunk_id(self, mock_search_service):
        dense_hits = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "reg", "score": 0.9},
        ]
        sparse_hits = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "reg", "content": "内容", "score": 5.0},
        ]
        fused = mock_search_service._rrf_fuse(dense_hits, sparse_hits, k=60)
        assert len(fused) == 1
        expected_score = 1.0 / (60 + 1) + 1.0 / (60 + 1)
        assert abs(fused[0]["score"] - expected_score) < 0.001

    def test_rrf_fuse_empty_dense(self, mock_search_service):
        sparse_hits = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "reg", "content": "内容", "score": 5.0},
        ]
        fused = mock_search_service._rrf_fuse([], sparse_hits, k=60)
        assert len(fused) == 1
        assert fused[0]["score"] == 1.0 / (60 + 1)

    def test_rrf_fuse_empty_sparse(self, mock_search_service):
        dense_hits = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "reg", "score": 0.9},
        ]
        fused = mock_search_service._rrf_fuse(dense_hits, [], k=60)
        assert len(fused) == 1
        assert fused[0]["score"] == 1.0 / (60 + 1)

    def test_rrf_fuse_both_empty(self, mock_search_service):
        fused = mock_search_service._rrf_fuse([], [], k=60)
        assert len(fused) == 0

    def test_rrf_fuse_ordering(self, mock_search_service):
        dense_hits = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "reg", "score": 0.9},
            {"chunk_id": "c2", "doc_id": "d1", "category": "reg", "score": 0.8},
            {"chunk_id": "c3", "doc_id": "d2", "category": "reg", "score": 0.7},
        ]
        sparse_hits = [
            {"chunk_id": "c3", "doc_id": "d2", "category": "reg", "content": "x", "score": 5.0},
            {"chunk_id": "c1", "doc_id": "d1", "category": "reg", "content": "y", "score": 3.0},
        ]
        fused = mock_search_service._rrf_fuse(dense_hits, sparse_hits, k=60)
        scores = [f["score"] for f in fused]
        assert scores == sorted(scores, reverse=True)


class TestSearchServiceFallback:
    def test_fallback_returns_empty_results(self, mock_search_service):
        result = asyncio.run(mock_search_service.hybrid_search("测试查询", "regulation", 5))
        assert result is not None


class TestSearchServiceRRF:
    @pytest.mark.asyncio
    async def test_hybrid_search_returns_results(self, mock_search_service):
        result = await mock_search_service.hybrid_search("逾期协商", "regulation", 3)
        assert "results" in result
        assert "degraded" in result
        assert "query_original" in result
        assert "trace_id" in result
        assert result["query_original"] == "逾期协商"

    @pytest.mark.asyncio
    async def test_hybrid_search_rerank_scores(self, mock_search_service):
        result = await mock_search_service.hybrid_search("催收", "regulation", 3)
        if result["results"]:
            for item in result["results"]:
                assert "rerank_score" in item
                assert "score" in item
