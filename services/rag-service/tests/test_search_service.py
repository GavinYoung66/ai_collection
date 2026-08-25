"""search_service 召回 UUID 容错测试（rag-service-final-fix 5.13/5.14）。"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from app.services.search_service import SearchService, _safe_uuid


def _make_svc_with_doc_repo():
    class MockLogService:
        async def log(self, **kwargs):
            pass

    class MockDocRepo:
        def __init__(self):
            self.db = MagicMock()
            empty = MagicMock()
            empty.scalars.return_value.all.return_value = []
            self.db.execute = AsyncMock(return_value=empty)

        async def get_titles_map(self, uid_list):
            return {}

        async def get_meta_dict(self, uid_list):
            return {}

    class MockMilvus:
        async def search(self, query_vector, top_n=20, category=None):
            return [
                {
                    "chunk_id": "not-a-uuid",
                    "doc_id": "doc-1",
                    "category": "regulation",
                    "score": 0.9,
                },
            ]

    class MockES:
        async def search(self, query, top_n=20, category=None):
            return [
                {
                    "chunk_id": "not-a-uuid",
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
                {"chunk_id": c["chunk_id"], "rerank_score": 0.9}
                for c in candidates
            ]

    return SearchService(
        milvus_client=MockMilvus(),
        es_client=MockES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
        doc_repo=MockDocRepo(),
    )


class TestInvalidUuidTolerance:
    @pytest.mark.asyncio
    async def test_invalid_uuid_in_recall_skipped_with_warning(self):
        """召回含非法 UUID → 单条跳过并记录 warning，检索不中断。"""
        svc = _make_svc_with_doc_repo()
        with capture_logs() as cap:
            result = await svc.hybrid_search("逾期协商", "regulation", 5)
        # 检索正常返回（未抛 ValueError）
        assert isinstance(result, dict)
        assert any(e["event"] == "recall_invalid_uuid_skipped" for e in cap)

    def test_safe_uuid_helper(self):
        assert _safe_uuid("not-a-uuid") is None
        assert _safe_uuid("") is None
        assert _safe_uuid(None) is None
        uid = uuid.uuid4()
        assert _safe_uuid(str(uid)) == uid
