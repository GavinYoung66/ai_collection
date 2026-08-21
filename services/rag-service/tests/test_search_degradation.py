"""Milvus / ES 宕机降级检索测试（DD-03 §9.2 断言15/16）。

模拟向量库 / 全文检索引擎不可用（连接超时、拒绝连接），
验证降级分支被触发：单侧宕机走另一侧检索并标记 degraded，
双侧宕机返回 A3005。全部使用 mock，不真实打挂服务。
"""
import pytest

from app.services.search_service import SearchService
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


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
