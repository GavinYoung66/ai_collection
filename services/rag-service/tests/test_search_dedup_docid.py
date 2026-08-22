"""同 doc_id 去重单元测试（§16.9）。

覆盖：
- RRF 融合后同 doc_id 三不同 chunk → 只留最高分的 1 块
- 单 chunk 文档不变
"""
from unittest.mock import MagicMock


from app.services.search_service import SearchService


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
    def test_three_chunks_same_doc_keeps_highest(self):
        """同 doc_id 三个不同 chunk → 去重后仅保留最高分。"""
        svc = _make_svc()
        fused = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.030},
            {"chunk_id": "c2", "doc_id": "d1", "category": "regulation", "score": 0.050},
            {"chunk_id": "c3", "doc_id": "d1", "category": "regulation", "score": 0.020},
        ]
        result = svc._dedup_by_doc_id(fused)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c2"
        assert result[0]["score"] == 0.050

    def test_single_chunk_unchanged(self):
        """单 chunk 文档 → 去重后不变。"""
        svc = _make_svc()
        fused = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.040},
        ]
        result = svc._dedup_by_doc_id(fused)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c1"

    def test_multiple_docs_each_keeps_highest(self):
        """多文档场景：每个 doc_id 各保留最高分 chunk。"""
        svc = _make_svc()
        fused = [
            {"chunk_id": "c1", "doc_id": "d1", "score": 0.03},
            {"chunk_id": "c2", "doc_id": "d1", "score": 0.05},
            {"chunk_id": "c3", "doc_id": "d2", "score": 0.04},
            {"chunk_id": "c4", "doc_id": "d2", "score": 0.02},
            {"chunk_id": "c5", "doc_id": "d3", "score": 0.01},
        ]
        result = svc._dedup_by_doc_id(fused)
        assert len(result) == 3
        doc_ids = {r["doc_id"] for r in result}
        assert doc_ids == {"d1", "d2", "d3"}
        # 各 doc 最高分
        by_doc = {r["doc_id"]: r for r in result}
        assert by_doc["d1"]["chunk_id"] == "c2"
        assert by_doc["d2"]["chunk_id"] == "c3"
        assert by_doc["d3"]["chunk_id"] == "c5"

    def test_empty_fused_returns_empty(self):
        """空输入 → 空输出。"""
        svc = _make_svc()
        assert svc._dedup_by_doc_id([]) == []
