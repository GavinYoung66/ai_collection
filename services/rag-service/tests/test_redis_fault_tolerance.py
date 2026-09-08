"""Redis 故障容错单元测试（D2 / 19.2）。

覆盖：
- rerank.is_degraded() 抛异常 → 按"未熔断"处理，检索正常返回
- session_svc.record() 抛异常 → 不阻断检索返回
- 上述场景检索接口不抛 500（服务层正常返回）
"""
import pytest

from app.services.search_service import SearchService
from app.settings import settings


class MockMilvus:
    async def search(self, query_vector, top_n=20, category=None):
        return [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.9},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script", "score": 0.8},
        ]


class MockES:
    async def search(self, query, top_n=20, category=None):
        return [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "content": "内容1", "score": 5.0},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script", "content": "内容2", "score": 3.0},
        ]


class MockEmbed:
    async def embed(self, text):
        return [0.1] * 1024


class MockLogService:
    async def log(self, **kwargs):
        pass


class FailingDegradedRerank:
    """is_degraded 抛异常，rerank 正常。"""

    async def is_degraded(self):
        raise RuntimeError("redis connection refused")

    async def rerank(self, query, candidates):
        return [{"chunk_id": c["chunk_id"], "rerank_score": 0.9} for c in candidates]


class FailingRecordSession:
    """record 抛异常，get_recent_queries 正常。"""

    async def get_recent_queries(self, session_id, lookback):
        return []

    async def record(self, **kwargs):
        raise RuntimeError("redis connection refused")


class TestRerankIsDegradedFaultTolerance:
    @pytest.mark.asyncio
    async def test_is_degraded_exception_treated_as_not_degraded(self):
        """is_degraded 抛异常 → 按未熔断处理，检索正常返回。"""
        svc = SearchService(
            milvus_client=MockMilvus(),
            es_client=MockES(),
            embed_client=MockEmbed(),
            rerank_client=FailingDegradedRerank(),
            log_service=MockLogService(),
            doc_repo=None,
        )
        result = await svc.hybrid_search("测试", None, 5)
        assert len(result["results"]) == 2


class TestSessionRecordFaultTolerance:
    @pytest.mark.asyncio
    async def test_session_record_exception_not_blocking(self, monkeypatch):
        """session record 抛异常 → 仅 warning，检索仍正常返回。"""
        monkeypatch.setattr(settings, "KB_MULTITURN_ENABLED", True)
        svc = SearchService(
            milvus_client=MockMilvus(),
            es_client=MockES(),
            embed_client=MockEmbed(),
            rerank_client=FailingDegradedRerank(),
            log_service=MockLogService(),
            doc_repo=None,
            session_service=FailingRecordSession(),
        )
        result = await svc.hybrid_search("测试", None, 5, session_id="sess-1")
        assert len(result["results"]) == 2
