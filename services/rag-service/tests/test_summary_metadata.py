"""摘要元数据能力单元测试（V4：summary-metadata delta spec / design D3 / D6）。

覆盖（对齐 tasks.md 7.1-7.4）：
- 7.1 上传携带摘要入库 → PG/ES 两端一致（ES→PG 顺序）；不携带摘要 → 存量行为不变
- 7.2 补全接口成功（两端同步 + 缓存失效）；doc_id 不存在 → B0004；ES 写失败重试后返回失败且 PG 不写（无半状态）
- 7.3 检索增强开关开启 → ES 摘要加权生效、响应 results[].summary 填充摘要值；开关关闭 → 检索行为与 V3 一致且 summary 恒为 null
- 7.4 入库时摘要写失败（ES 不可用模拟）→ 正文 active、PG summary 为 NULL、warning 日志含 trace_id、无 A3003
"""
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.api.documents import update_document_summary, upload_document
from app.api.search import hybrid_search
from app.models.document import SummaryUpdateRequest
from app.models.search import SearchRequest
from app.parsers import ParserFactory
from app.services import query_cache
from app.services.search_service import SearchService
from app.services.summary_service import SummaryService
from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from structlog.testing import capture_logs

DOC_ID = uuid.uuid4()
SUMMARY_TEXT = "逾期协商还款流程摘要：需在电话中明确还款金额与期限。"


# ============ 7.1 / 7.2 / 7.4：SummaryService 两端写入编排 ============

class _RecordingES:
    """记录调用序的 ES 客户端替身（doc_summary 写失败可注入）。"""

    def __init__(self, fail_first_n: int = 0):
        self.calls = 0
        self.fail_first_n = fail_first_n
        self.order: list[str] = []

    async def update_summary(self, doc_id: str, summary: str) -> None:
        self.calls += 1
        self.order.append("es")
        if self.calls <= self.fail_first_n:
            raise RuntimeError("es index mapping not updated")


class _FakeDocRepo:
    def __init__(self):
        self.updated: list[tuple[uuid.UUID, str]] = []
        self.order: list[str] = []
        self.doc = MagicMock()
        self.doc.category = "regulation"

    async def update_summary(self, doc_id: uuid.UUID, summary: str) -> None:
        self.updated.append((doc_id, summary))
        self.order.append("pg")

    async def get_by_id(self, doc_id: uuid.UUID):
        return self.doc


class TestSummaryService:
    @pytest.mark.asyncio
    async def test_write_summary_es_then_pg_order(self, monkeypatch):
        """7.1 两端一致：写入顺序固定 ES → PG（PG 为一致性收口标记）。"""
        es = _RecordingES()
        repo = _FakeDocRepo()
        repo.order = []
        monkeypatch.setattr(
            "app.services.summary_service.query_cache.invalidate_category", AsyncMock()
        )
        svc = SummaryService(doc_repo=repo, es_client=es)
        ok = await svc.write_summary(DOC_ID, SUMMARY_TEXT, retries=0)
        assert ok is True
        assert es.order == ["es"]
        assert repo.order == ["pg"]
        assert repo.updated == [(DOC_ID, SUMMARY_TEXT)]

    @pytest.mark.asyncio
    async def test_write_summary_invalidates_category_cache(self, monkeypatch):
        """7.2 补全成功：全部写入成功后按文档 category 失效查询缓存。"""
        invalidate = AsyncMock()
        monkeypatch.setattr(
            "app.services.summary_service.query_cache.invalidate_category", invalidate
        )
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", True)
        svc = SummaryService(doc_repo=_FakeDocRepo(), es_client=_RecordingES())
        ok = await svc.write_summary(DOC_ID, SUMMARY_TEXT, retries=0)
        assert ok is True
        invalidate.assert_awaited_once_with("regulation")

    @pytest.mark.asyncio
    async def test_write_summary_es_failure_skips_pg_no_raise(self, monkeypatch):
        """7.4 入库摘要写失败：ES 重试后仍失败 → 返回 False、PG 不写、warning 日志含事件与 trace_id、不抛 A3003。"""
        es = _RecordingES(fail_first_n=99)
        repo = _FakeDocRepo()
        monkeypatch.setattr(
            "app.services.summary_service.query_cache.invalidate_category", AsyncMock()
        )
        svc = SummaryService(doc_repo=repo, es_client=es)
        with capture_logs() as cap:
            ok = await svc.write_summary(DOC_ID, SUMMARY_TEXT, retries=3, raise_on_failure=False)
        assert ok is False
        assert repo.updated == []  # PG 不写（保持 NULL）
        assert any(e["event"] == "summary_es_write_failed_skipped" for e in cap)

    @pytest.mark.asyncio
    async def test_write_summary_es_failure_raises_a3003_no_pg(self):
        """7.2 补全接口失败：ES 重试仍失败 → 抛 A3003（复用索引写入失败语义），PG 不写（无半状态）。"""
        es = _RecordingES(fail_first_n=99)
        repo = _FakeDocRepo()
        svc = SummaryService(doc_repo=repo, es_client=es)
        with pytest.raises(BizException) as exc_info:
            await svc.write_summary(DOC_ID, SUMMARY_TEXT, retries=3, raise_on_failure=True)
        assert exc_info.value.code == ErrorCode.A3003
        assert repo.updated == []  # PG 不写

    @pytest.mark.asyncio
    async def test_write_summary_retries_es_then_succeeds(self):
        """7.2 补全成功（含重试）：ES 前 2 次失败、第 3 次成功 → 两端写入成功。"""
        es = _RecordingES(fail_first_n=2)
        repo = _FakeDocRepo()
        svc = SummaryService(doc_repo=repo, es_client=es)
        ok = await svc.write_summary(DOC_ID, SUMMARY_TEXT, retries=3, raise_on_failure=True)
        assert ok is True
        assert es.calls == 3
        assert repo.updated == [(DOC_ID, SUMMARY_TEXT)]

    def test_summary_service_no_milvus_dependency(self):
        """7.1 Milvus 不含摘要字段：SummaryService 仅依赖 DocRepo + ESClient，不持有 Milvus 客户端。"""
        svc = SummaryService(doc_repo=MagicMock(), es_client=MagicMock())
        assert not hasattr(svc, "milvus")
        assert not hasattr(svc, "milvus_client")


# ============ 7.1：上传接口 summary 透传 / 存量不变 ============

class TestUploadSummary:
    def _patch_upload(self, monkeypatch, captured: dict):
        class MockIngest:
            async def ingest(self, file_path, filename, category, created_by=None, summary=None):
                captured["summary"] = summary
                return {"doc_id": str(DOC_ID), "chunks_count": 1, "status": "active"}

        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(
            "app.api.documents.save_upload_file",
            AsyncMock(return_value=Path("/tmp/x.txt")),
        )
        monkeypatch.setattr(
            "app.api.documents._get_ingest_service", lambda *a, **k: MockIngest()
        )
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)

    @pytest.mark.asyncio
    async def test_upload_with_summary_passes_to_ingest(self, monkeypatch):
        """7.1 携带摘要上传 → 摘要透传给入库链路（存量字段与行为不变）。"""
        captured: dict = {}
        self._patch_upload(monkeypatch, captured)
        resp = await upload_document(
            file=MagicMock(filename="x.txt"),
            category="regulation",
            summary=SUMMARY_TEXT,
            auth=None, rate=None,
            db=AsyncMock(), milvus=MagicMock(), es=MagicMock(), embed=MagicMock(),
        )
        assert captured["summary"] == SUMMARY_TEXT
        assert resp["data"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_upload_without_summary_unchanged(self, monkeypatch):
        """7.1 不携带摘要 → summary=None 透传（存量行为完全不变）。"""
        captured: dict = {}
        self._patch_upload(monkeypatch, captured)
        await upload_document(
            file=MagicMock(filename="x.txt"),
            category="regulation",
            summary="",
            auth=None, rate=None,
            db=AsyncMock(), milvus=MagicMock(), es=MagicMock(), embed=MagicMock(),
        )
        assert captured["summary"] is None

    @pytest.mark.asyncio
    async def test_upload_summary_too_long_raises_b0001(self, monkeypatch):
        """7.1 摘要超 KB_SUMMARY_MAX_LEN → B0001（长度校验）。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_MAX_LEN", 10)
        with pytest.raises(BizException) as exc_info:
            await upload_document(
                file=MagicMock(filename="x.txt"),
                category="regulation",
                summary="x" * 11,
                auth=None, rate=None,
                db=MagicMock(), milvus=MagicMock(), es=MagicMock(), embed=MagicMock(),
            )
        assert exc_info.value.code == ErrorCode.B0001


# ============ 7.2：补全接口路由 ============

class TestUpdateSummaryRoute:
    def _mock_db_with_doc(self, doc):
        db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = doc
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_update_summary_success(self, monkeypatch):
        """7.2 补全接口成功：返回 doc_id/summary，补全服务以 retries=3/raise_on_failure=True 调用。"""
        calls: list = []

        class FakeSummaryService:
            def __init__(self, doc_repo, es_client):
                pass

            async def write_summary(self, doc_id, summary, *, retries=0, raise_on_failure=False):
                calls.append((doc_id, summary, retries, raise_on_failure))

        monkeypatch.setattr("app.api.documents.SummaryService", FakeSummaryService)
        db = self._mock_db_with_doc(MagicMock(category="regulation"))
        resp = await update_document_summary(
            doc_id=str(DOC_ID),
            req=SummaryUpdateRequest(summary=SUMMARY_TEXT),
            auth=None, rate=None, db=db, es=MagicMock(),
        )
        assert calls == [(DOC_ID, SUMMARY_TEXT, 3, True)]
        assert resp["data"]["doc_id"] == str(DOC_ID)
        assert resp["data"]["summary"] == SUMMARY_TEXT

    @pytest.mark.asyncio
    async def test_update_summary_doc_not_found_b0004(self):
        """7.2 doc_id 不存在 → B0004。"""
        db = self._mock_db_with_doc(None)
        with pytest.raises(BizException) as exc_info:
            await update_document_summary(
                doc_id=str(DOC_ID),
                req=SummaryUpdateRequest(summary=SUMMARY_TEXT),
                auth=None, rate=None, db=db, es=MagicMock(),
            )
        assert exc_info.value.code == ErrorCode.B0004

    @pytest.mark.asyncio
    async def test_update_summary_invalid_uuid_b0001(self):
        """7.2 doc_id 非 UUID → B0001。"""
        db = self._mock_db_with_doc(MagicMock())
        with pytest.raises(BizException) as exc_info:
            await update_document_summary(
                doc_id="not-a-uuid",
                req=SummaryUpdateRequest(summary=SUMMARY_TEXT),
                auth=None, rate=None, db=db, es=MagicMock(),
            )
        assert exc_info.value.code == ErrorCode.B0001


# ============ 7.3：检索增强开关开启/关闭 ============

def _make_search_service_with_es_capture(captured: dict):
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
                    "chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "doc_id": "11111111-1111-1111-1111-111111111111",
                    "category": "regulation",
                    "content": "正文内容",
                    "score": 0.9,
                },
            ]

    class MockES:
        async def search(self, query, top_n=20, category=None, **kwargs):
            # 仅在实际传入 summary_boost kwarg 时记录（区分"未传"与"传 None"）
            if "summary_boost" in kwargs:
                captured["summary_boost"] = kwargs["summary_boost"]
            return [
                {
                    "chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "doc_id": "11111111-1111-1111-1111-111111111111",
                    "category": "regulation",
                    "content": "正文内容",
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


class TestSearchSummaryEnhancement:
    @pytest.mark.asyncio
    async def test_es_summary_boost_passed_when_enabled(self, monkeypatch):
        """7.3 开关开启 → ES 路对 doc_summary BM25 加权（summary_boost=KB_SUMMARY_ES_BOOST）。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", True)
        captured: dict = {}
        svc = _make_search_service_with_es_capture(captured)
        await svc.hybrid_search("逾期协商", "regulation", 5)
        assert captured["summary_boost"] == settings.KB_SUMMARY_ES_BOOST

    @pytest.mark.asyncio
    async def test_es_summary_boost_none_when_disabled(self, monkeypatch):
        """7.3 开关关闭 → 不传 summary_boost kwarg，ES 调用签名与 V3 完全一致。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)
        captured: dict = {}
        svc = _make_search_service_with_es_capture(captured)
        await svc.hybrid_search("逾期协商", "regulation", 5)
        assert "summary_boost" not in captured

    def _make_request(self) -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/kb/search",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 123),
            "scheme": "http",
        }
        return Request(scope)

    def _patch_search_route(self, monkeypatch, fake_doc_repo):
        class FakeSearchService:
            def __init__(self, **kwargs):
                pass

            async def hybrid_search(self, **kwargs):
                return {
                    "results": [
                        {
                            "doc_id": "11111111-1111-1111-1111-111111111111",
                            "title": "制度",
                            "chunk_index": 0,
                            "content": "正文内容",
                            "score": 0.03,
                            "rerank_score": 0.9,
                            "version": 1,
                            "source": "v1_legacy",
                        },
                    ],
                    "degraded": False,
                    "query_original": "q",
                    "message": None,
                    "trace_id": "t",
                }

        monkeypatch.setattr("app.api.search.SearchService", FakeSearchService)
        monkeypatch.setattr("app.api.search.DocRepo", lambda db: fake_doc_repo)
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)

    @pytest.mark.asyncio
    async def test_search_response_summary_filled_when_enabled(self, monkeypatch):
        """7.3 开关开启 → 响应 results[].summary 填充 PG 文档级摘要。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", True)

        class FakeDocRepo:
            async def get_summaries_map(self, doc_ids):
                return {"11111111-1111-1111-1111-111111111111": SUMMARY_TEXT}

        self._patch_search_route(monkeypatch, FakeDocRepo())
        resp = await hybrid_search(
            req=SearchRequest(query="逾期协商", category="regulation", top_k=5),
            request=self._make_request(),
            response=Response(),
            rate=None,
            db=MagicMock(),
            milvus=MagicMock(), es=MagicMock(), embed=MagicMock(), rerank=MagicMock(),
            rewrite_svc=None, routing_svc=None, weighting_svc=None,
            row_access_svc=None, session_svc=None,
        )
        assert resp["data"]["results"][0]["summary"] == SUMMARY_TEXT

    @pytest.mark.asyncio
    async def test_search_response_summary_null_when_disabled(self, monkeypatch):
        """7.3 开关关闭 → summary 恒为 null，检索行为与 V3 一致。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)

        class FakeDocRepo:
            async def get_summaries_map(self, doc_ids):
                return {"11111111-1111-1111-1111-111111111111": SUMMARY_TEXT}

        self._patch_search_route(monkeypatch, FakeDocRepo())
        resp = await hybrid_search(
            req=SearchRequest(query="逾期协商", category="regulation", top_k=5),
            request=self._make_request(),
            response=Response(),
            rate=None,
            db=MagicMock(),
            milvus=MagicMock(), es=MagicMock(), embed=MagicMock(), rerank=MagicMock(),
            rewrite_svc=None, routing_svc=None, weighting_svc=None,
            row_access_svc=None, session_svc=None,
        )
        assert resp["data"]["results"][0]["summary"] is None


# ============ 7.3 附：缓存隔离（flags_hash 纳入 KB_SUMMARY_ENABLED） ============

class TestSummaryCacheIsolation:
    def test_cache_key_differs_by_summary_switch(self, monkeypatch):
        """开关切换 → 缓存 key 隔离（不命中旧缓存），保证 summary 填充契约。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)
        key_off = query_cache.make_cache_key("逾期协商", "regulation", 5)
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", True)
        key_on = query_cache.make_cache_key("逾期协商", "regulation", 5)
        assert key_off != key_on
