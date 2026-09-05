"""Embedding 熔断 ES-only 降级测试（tasks §20.2，评审缺陷修复验证）。

背景：hybrid-search spec 闸门 1 约定「Embedding 熔断走 ES-only 降级路径
（dense_hits 空、max_dense_score=None）」。修复后检索侧 Embedding 失败
不再抛 A3003，而是降级为仅 ES BM25 召回；双路均不可用才抛 A3005。
"""
import pytest

from app.services.search_service import SearchService
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


def _make_service(
    embed_raises: bool = False,
    es_raises: bool = False,
    sparse_hits: list[dict] | None = None,
    rerank_score: float = 0.9,
):
    """构造 SearchService 测试替身。

    Args:
        embed_raises: True 时 Embedding 抛异常（模拟熔断/宕机）。
        es_raises: True 时 ES BM25 召回抛异常。
        sparse_hits: ES 返回的命中；None 时使用默认单条命中。
        rerank_score: Rerank 返回的精排分数。
    """
    log_calls = []

    class MockLogService:
        async def log(self, **kwargs):
            log_calls.append(kwargs)

    class MockMilvus:
        async def search(self, query_vector, top_n=20, category=None):
            return [
                {
                    "chunk_id": "chunk-m",
                    "doc_id": "doc-m",
                    "category": "regulation",
                    "score": 0.9,
                },
            ]

    class MockES:
        async def search(self, query, top_n=20, category=None):
            if es_raises:
                raise ConnectionError("es down")
            if sparse_hits is not None:
                return sparse_hits
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
            if embed_raises:
                raise RuntimeError("Embedding service is in circuit-break state")
            return [0.1] * 1024

    class MockRerank:
        async def is_degraded(self):
            return False

        async def rerank(self, query, candidates):
            return [
                {"chunk_id": c["chunk_id"], "rerank_score": rerank_score}
                for c in candidates
            ]

    svc = SearchService(
        milvus_client=MockMilvus(),
        es_client=MockES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
    )
    return svc, log_calls


class TestEmbedEsOnlyDegradation:
    @pytest.mark.asyncio
    async def test_embed_failure_degrades_to_es_only(self):
        """T1: Embedding 失败 → 降级 ES-only，返回结果且 degraded=true，不抛 A3003。"""
        svc, log_calls = _make_service(embed_raises=True)
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["degraded"] is True
        assert result["results"] != []
        assert result["message"] is None
        # 日志：dense_hit_count=0（未走 Milvus），sparse_hit_count>0
        last = log_calls[-1]
        assert last["dense_hit_count"] == 0
        assert last["sparse_hit_count"] > 0
        assert last["is_fallback"] is False

    @pytest.mark.asyncio
    async def test_embed_failure_and_es_failure_raises_a3005(self):
        """T2: Embedding + ES 双失败 → 抛 A3005（双路均不可用）。"""
        svc, _ = _make_service(embed_raises=True, es_raises=True)
        with pytest.raises(BizException) as exc_info:
            await svc.hybrid_search("测试", "regulation", 5)
        assert exc_info.value.code == ErrorCode.A3005

    @pytest.mark.asyncio
    async def test_embed_failure_gate2_fallback(self):
        """T3: Embedding 失败 + rerank 低分(0.2<0.35) → 闸门 2 兜底文案。"""
        svc, log_calls = _make_service(embed_raises=True, rerank_score=0.2)
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["results"] == []
        assert result["message"] is not None
        assert log_calls[-1]["is_fallback"] is True

    @pytest.mark.asyncio
    async def test_embed_failure_es_only_rrf_dedup_traceback(self):
        """T4: ES-only 下 RRF/去重/溯源正常（同 doc_id 可保留多条 top-N）。"""
        sparse_hits = [
            {
                "chunk_id": "chunk-a",
                "doc_id": "doc-1",
                "category": "regulation",
                "content": "内容A",
                "score": 8.0,
            },
            {
                "chunk_id": "chunk-b",
                "doc_id": "doc-1",
                "category": "regulation",
                "content": "内容B",
                "score": 5.0,
            },
        ]
        svc, _ = _make_service(embed_raises=True, sparse_hits=sparse_hits)
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert len(result["results"]) >= 1
        assert all(r["doc_id"] == "doc-1" for r in result["results"])
        assert result["results"][0]["version"] == 1
        assert result["results"][0]["source"] == "v1_legacy"

    @pytest.mark.asyncio
    async def test_embed_ok_normal_path_unchanged(self):
        """T5: Embedding 正常 → 双路召回不受影响，degraded=false（回归保障）。"""
        svc, log_calls = _make_service(embed_raises=False)
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["degraded"] is False
        assert result["results"] != []
        last = log_calls[-1]
        assert last["dense_hit_count"] > 0
        assert last["sparse_hit_count"] > 0
