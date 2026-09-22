"""Milvus / ES 宕机降级检索测试（DD-03 §9.2 断言15/16）。

模拟向量库 / 全文检索引擎不可用（连接超时、拒绝连接），
验证降级分支被触发：单侧宕机走另一侧检索并标记 degraded，
双侧宕机返回 A3005。全部使用 mock，不真实打挂服务。
"""
import pytest

from app.services.search_service import SearchService
from app.vectorstore.rerank_client import RerankClient
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


async def _trip_open(breaker) -> None:
    for _ in range(breaker.fail_max):
        await breaker.record_failure(RuntimeError("down"))


class MockEmbed:
    async def embed(self, text):
        return [0.1] * 1024


class MockRerank:
    async def is_degraded(self):
        return False

    async def rerank(self, query, candidates):
        return [
            {"chunk_id": c["chunk_id"], "rerank_score": float(0.9 - i * 0.3)}
            for i, c in enumerate(candidates)
        ]


class MockLog:
    async def log(self, **kwargs):
        pass


class FailingMilvus:
    async def search(self, query_vector, top_n=20, category=None):
        raise ConnectionError("Milvus gRPC 连接超时")


class FailingES:
    async def search(self, query, top_n=20, category=None):
        raise ConnectionError("ES 连接拒绝")


class WorkingES:
    async def search(self, query, top_n=20, category=None):
        return [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "category": "regulation",
                "content": "ES 命中内容",
                "score": 5.0,
            },
        ]


class WorkingMilvus:
    async def search(self, query_vector, top_n=20, category=None):
        return [
            {"chunk_id": "c2", "doc_id": "d2", "category": "regulation", "score": 0.9},
        ]


def _service(milvus, es) -> SearchService:
    return SearchService(
        milvus_client=milvus,
        es_client=es,
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLog(),
    )


@pytest.mark.asyncio
async def test_milvus_down_es_only_returns_degraded():
    svc = _service(FailingMilvus(), WorkingES())
    result = await svc.hybrid_search("协商还款", "regulation", 5)
    assert result["degraded"] is True
    assert len(result["results"]) == 1
    assert result["results"][0]["content"] == "ES 命中内容"


@pytest.mark.asyncio
async def test_es_down_milvus_only_returns_degraded():
    svc = _service(WorkingMilvus(), FailingES())
    result = await svc.hybrid_search("协商还款", "regulation", 5)
    assert result["degraded"] is True
    assert len(result["results"]) == 1
    assert result["results"][0]["doc_id"] == "d2"


@pytest.mark.asyncio
async def test_both_down_raises_a3005():
    svc = _service(FailingMilvus(), FailingES())
    with pytest.raises(BizException) as exc:
        await svc.hybrid_search("协商还款", "regulation", 5)
    assert exc.value.code == ErrorCode.A3005


class TwoHitMilvus:
    async def search(self, query_vector, top_n=20, category=None):
        return [
            {"chunk_id": "c-high", "doc_id": "d-high", "category": "regulation", "score": 0.95},
            {"chunk_id": "c-low", "doc_id": "d-low", "category": "regulation", "score": 0.4},
        ]


class TwoHitES:
    async def search(self, query, top_n=20, category=None):
        return [
            {
                "chunk_id": "c-high",
                "doc_id": "d-high",
                "category": "regulation",
                "content": "高分原文",
                "score": 10.0,
            },
            {
                "chunk_id": "c-low",
                "doc_id": "d-low",
                "category": "regulation",
                "content": "低分原文",
                "score": 1.0,
            },
        ]


@pytest.mark.asyncio
async def test_rerank_breaker_open_uses_original_scores():
    """breaker open → 跳过精排，按 RRF/原始分排序，degraded=true，非 500。"""
    rerank = RerankClient()
    await _trip_open(rerank._breaker)
    assert rerank._breaker.state == "open"
    assert await rerank.is_degraded() is True

    http_calls = []

    async def boom_rerank(query, candidates):
        http_calls.append((query, candidates))
        raise AssertionError("rerank HTTP must be skipped when breaker is open")

    rerank.rerank = boom_rerank  # type: ignore[method-assign]
    svc = SearchService(
        milvus_client=TwoHitMilvus(),
        es_client=TwoHitES(),
        embed_client=MockEmbed(),
        rerank_client=rerank,
        log_service=MockLog(),
    )
    result = await svc.hybrid_search("协商还款", "regulation", 5)
    assert result["degraded"] is True
    assert len(result["results"]) >= 2
    assert result["results"][0]["content"] == "高分原文"
    assert result["results"][0]["score"] >= result["results"][1]["score"]
    assert http_calls == []
    assert rerank._redis is None  # 未走 rag:rerank:degraded SETEX
