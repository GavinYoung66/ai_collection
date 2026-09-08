"""行级权限过滤 fail-closed 单元测试（D1 / 19.3）。

覆盖：
- 矩阵异常 → 保底矩阵 (default,default) 重放过滤
- 保底矩阵仍失败 → 空 results（不抛异常、不返回越权数据）
- 开关关闭 → filter 不被调用，零影响
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


class MockRerank:
    async def is_degraded(self):
        return False

    async def rerank(self, query, candidates):
        return [{"chunk_id": c["chunk_id"], "rerank_score": 0.9} for c in candidates]


class MockLogService:
    async def log(self, **kwargs):
        pass


class RaisingRowAccess:
    """filter 抛异常，但 get_default_matrix 可用。"""

    def filter(self, items, jwt_payload, category_filter=None):
        raise RuntimeError("matrix load error")

    def get_default_matrix(self):
        return {("default", "default"): ["regulation", "script", "case", "other"]}


class TotalFailRowAccess:
    """filter 与 get_default_matrix 均抛异常。"""

    def filter(self, items, jwt_payload, category_filter=None):
        raise RuntimeError("matrix load error")

    def get_default_matrix(self):
        raise RuntimeError("fallback matrix error")


class NeverCalledRowAccess(RaisingRowAccess):
    def __init__(self):
        self.filter_called = False

    def filter(self, items, jwt_payload, category_filter=None):
        self.filter_called = True
        raise RuntimeError("should not be called")


def _make_svc(row_access_svc):
    return SearchService(
        milvus_client=MockMilvus(),
        es_client=MockES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
        doc_repo=None,
        row_access_service=row_access_svc,
    )


class TestRowAccessFailClosed:
    @pytest.mark.asyncio
    async def test_matrix_exception_replays_default_matrix(self, monkeypatch):
        """filter 异常 → 保底矩阵全量放行，结果正常返回。"""
        monkeypatch.setattr(settings, "KB_ROW_LEVEL_ENABLED", True)
        svc = _make_svc(RaisingRowAccess())
        result = await svc.hybrid_search(
            "测试", None, 5, jwt_payload={"role": "AGENT", "department": "催收一部"}
        )
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_fallback_matrix_fails_empty_results(self, monkeypatch):
        """保底矩阵也失败 → 空 results，不抛异常、不返回越权数据。"""
        monkeypatch.setattr(settings, "KB_ROW_LEVEL_ENABLED", True)
        svc = _make_svc(TotalFailRowAccess())
        result = await svc.hybrid_search(
            "测试", None, 5, jwt_payload={"role": "AGENT", "department": "催收一部"}
        )
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_switch_off_zero_impact(self, monkeypatch):
        """开关关闭 → filter 不被调用，结果正常返回。"""
        monkeypatch.setattr(settings, "KB_ROW_LEVEL_ENABLED", False)
        row_access = NeverCalledRowAccess()
        svc = _make_svc(row_access)
        result = await svc.hybrid_search(
            "测试", None, 5, jwt_payload={"role": "AGENT", "department": "催收一部"}
        )
        assert len(result["results"]) == 2
        assert row_access.filter_called is False
