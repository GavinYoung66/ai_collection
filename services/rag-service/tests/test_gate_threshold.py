"""V1-T17 整改测试：双闸门 0.6 接入检索流程。

闸门 1：稠密召回最高相似度 < RAG_SCORE_THRESHOLD(0.6) → 判定无匹配，走兜底；
ES-only 降级（无稠密相似度）跳过闸门 1，由闸门 2（rerank 0.35）兜底。
"""
import pytest

from app.services.search_service import SearchService
from common.config.settings import settings


def _make_service(dense_scores: list[float] | None, rerank_score: float = 0.9):
    class MockLogService:
        async def log(self, **kwargs):
            pass

    class MockMilvus:
        async def search(self, query_vector, top_n=20, category=None):
            if not dense_scores:
                raise ConnectionError("milvus down")
            return [
                {
                    "chunk_id": f"chunk-{i}",
                    "doc_id": f"doc-{i}",
                    "category": "regulation",
                    "score": s,
                }
                for i, s in enumerate(dense_scores, 1)
            ]

    class MockES:
        async def search(self, query, top_n=20, category=None):
            return [
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "doc-1",
                    "category": "regulation",
                    "content": "内容1",
                    "score": 5.0,
                },
            ]

    class MockEmbed:
        async def embed(self, text):
            return [0.1] * 1024

    class MockRerank:
        async def is_degraded(self):
            return False

        async def rerank(self, query, candidates):
            return [
                {"chunk_id": c["chunk_id"], "rerank_score": rerank_score}
                for c in candidates
            ]

    return SearchService(
        milvus_client=MockMilvus(),
        es_client=MockES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
    )


class TestGate1Threshold:
    @pytest.mark.asyncio
    async def test_low_dense_score_triggers_fallback(self):
        """稠密最高相似度 0.5 < 0.6 → 无匹配兜底，不执行 rerank 精排。"""
        svc = _make_service([0.5])
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["results"] == []
        assert result["message"] is not None
        assert result["query_original"] == "测试"

    @pytest.mark.asyncio
    async def test_high_dense_score_returns_results(self):
        """稠密最高相似度 0.9 >= 0.6 → 正常检索（rerank 0.9 不触发闸门 2）。"""
        svc = _make_service([0.9])
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["results"] != []
        assert result["message"] is None

    @pytest.mark.asyncio
    async def test_es_only_degraded_skips_gate1(self):
        """Milvus 宕机（ES-only 降级）→ 无稠密相似度，跳过闸门 1，正常返回结果。"""
        svc = _make_service(None)
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["degraded"] is True
        assert result["results"] != []

    @pytest.mark.asyncio
    async def test_boundary_score_exactly_threshold_returns(self, monkeypatch):
        """边界：稠密相似度恰等于 0.6 → 不触发闸门 1（>= 阈值放行）。"""
        monkeypatch.setattr(settings, "RAG_SCORE_THRESHOLD", 0.6)
        svc = _make_service([0.6])
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["results"] != []
