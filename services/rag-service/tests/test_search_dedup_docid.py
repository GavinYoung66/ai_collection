"""同 doc_id top-N 去重单元测试（fix-rag-same-doc-recall）。

覆盖：
- 同 doc_id 三不同 chunk 且 N≥3 → 保留 3 条
- 同 doc 8 chunk 且 N=5 → 保留最高 5 条
- 单 chunk 文档不变
- 多文档各保留 top-N
"""
from unittest.mock import MagicMock

import pytest

from app.services.search_service import SearchService
from app.settings import settings


def _make_svc():
    """构造仅用于 _dedup_by_doc_id 测试的 SearchService（依赖全 mock）。"""
    return SearchService(
        milvus_client=MagicMock(),
        es_client=MagicMock(),
        embed_client=MagicMock(),
        rerank_client=MagicMock(),
        log_service=MagicMock(),
    )


class TestDedupByDocId:
    def test_three_chunks_same_doc_keeps_top_n(self, monkeypatch):
        """同 doc_id 三个不同 chunk 且 N≥3 → 保留 3 条。"""
        monkeypatch.setattr(settings, "KB_DOC_CHUNK_KEEP", 5)
        svc = _make_svc()
        fused = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.030},
            {"chunk_id": "c2", "doc_id": "d1", "category": "regulation", "score": 0.050},
            {"chunk_id": "c3", "doc_id": "d1", "category": "regulation", "score": 0.020},
        ]
        result = svc._dedup_by_doc_id(fused, keep_n=3)
        assert len(result) == 3
        assert {r["chunk_id"] for r in result} == {"c1", "c2", "c3"}

    def test_eight_chunks_keeps_top_five(self):
        """同 doc 8 chunk 且 N=5 → 保留 RRF 最高 5 条。"""
        svc = _make_svc()
        fused = [
            {"chunk_id": f"c{i}", "doc_id": "d1", "score": float(i) / 100}
            for i in range(8)
        ]
        fused.sort(key=lambda x: x["score"], reverse=True)
        result = svc._dedup_by_doc_id(fused, keep_n=5)
        assert len(result) == 5
        kept_scores = sorted([r["score"] for r in result], reverse=True)
        assert kept_scores == [0.07, 0.06, 0.05, 0.04, 0.03]

    def test_single_chunk_unchanged(self):
        """单 chunk 文档 → 去重后不变。"""
        svc = _make_svc()
        fused = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.040},
        ]
        result = svc._dedup_by_doc_id(fused, keep_n=5)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c1"

    def test_multiple_docs_each_keeps_top_n(self):
        """多文档场景：每个 doc_id 各保留最高 N 条。"""
        svc = _make_svc()
        fused = [
            {"chunk_id": "c1", "doc_id": "d1", "score": 0.03},
            {"chunk_id": "c2", "doc_id": "d1", "score": 0.05},
            {"chunk_id": "c3", "doc_id": "d2", "score": 0.04},
            {"chunk_id": "c4", "doc_id": "d2", "score": 0.02},
            {"chunk_id": "c5", "doc_id": "d3", "score": 0.01},
        ]
        result = svc._dedup_by_doc_id(fused, keep_n=1)
        assert len(result) == 3
        by_doc = {r["doc_id"]: r for r in result}
        assert by_doc["d1"]["chunk_id"] == "c2"
        assert by_doc["d2"]["chunk_id"] == "c3"
        assert by_doc["d3"]["chunk_id"] == "c5"

    def test_empty_fused_returns_empty(self):
        """空输入 → 空输出。"""
        svc = _make_svc()
        assert svc._dedup_by_doc_id([], keep_n=5) == []

    def test_keep_n_minimum_one(self):
        """keep_n=0 时仍至少保留 1 条。"""
        svc = _make_svc()
        fused = [
            {"chunk_id": "c1", "doc_id": "d1", "score": 0.03},
            {"chunk_id": "c2", "doc_id": "d1", "score": 0.05},
        ]
        result = svc._dedup_by_doc_id(fused, keep_n=0)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c2"
