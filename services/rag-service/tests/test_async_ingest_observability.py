"""异步入库可观测性测试（async-kb-ingest-observability）。"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import documents as docs_module
from app.api.documents import upload_document
from app.repositories.doc_repo import STAGE_PROGRESS
from app.services.ingest_service import sanitize_ingest_error
from app.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


class TestSanitizeIngestError:
    def test_biz_exception_uses_message(self):
        err = BizException(ErrorCode.A3001, "文档解析失败")
        assert sanitize_ingest_error(err) == "文档解析失败"

    def test_generic_exception_masked(self):
        assert sanitize_ingest_error(RuntimeError("secret stack")) == (
            "入库失败，请稍后刷新列表或联系管理员"
        )


class TestStageProgressMapping:
    def test_stage_progress_constants(self):
        assert STAGE_PROGRESS["parse"] == 25
        assert STAGE_PROGRESS["body_index"] == 50
        assert STAGE_PROGRESS["asset_index"] == 75
        assert STAGE_PROGRESS["done"] == 100


class TestAsyncUploadApi:
    def _mock_db(self, *, processing_existing=None, doc_id=None):
        doc_id = doc_id or uuid.uuid4()
        db = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        repo = MagicMock()
        repo.find_processing_by_title = AsyncMock(return_value=processing_existing)
        repo.find_active_by_title = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=doc_id)
        repo.update_ingest_stage = AsyncMock()

        return db, repo, doc_id

    @pytest.mark.asyncio
    async def test_upload_returns_processing(self, monkeypatch, tmp_path):
        db, repo, doc_id = self._mock_db()
        scheduled: dict = {}

        async def fake_staging(file, did):
            p = tmp_path / "staged.txt"
            p.write_bytes(b"data")
            return p

        def fake_schedule(did, path, filename, category, summary):
            scheduled.update(
                dict(
                    doc_id=did,
                    path=path,
                    filename=filename,
                    category=category,
                    summary=summary,
                )
            )

        monkeypatch.setattr(docs_module, "DocRepo", lambda _db: repo)
        monkeypatch.setattr(docs_module.ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(docs_module, "save_upload_to_staging", fake_staging)
        monkeypatch.setattr(docs_module, "schedule_background_ingest", fake_schedule)
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)

        resp = await upload_document(
            file=MagicMock(filename="a.txt"),
            category="regulation",
            summary="",
            auth=None,
            rate=None,
            db=db,
        )
        assert resp["data"]["status"] == "processing"
        assert resp["data"]["doc_id"] == str(doc_id)
        assert scheduled["summary"] is None
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duplicate_processing_rejected(self, monkeypatch):
        db, repo, _ = self._mock_db(processing_existing=MagicMock())
        monkeypatch.setattr(docs_module, "DocRepo", lambda _db: repo)
        monkeypatch.setattr(docs_module.ParserFactory, "is_supported", lambda f: True)

        with pytest.raises(BizException) as exc_info:
            await upload_document(
                file=MagicMock(filename="dup.txt"),
                category="regulation",
                summary="",
                auth=None,
                rate=None,
                db=db,
            )
        assert exc_info.value.code == ErrorCode.B0001
        assert "正在入库" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_duplicate_active_rejected(self, monkeypatch):
        db, repo, _ = self._mock_db()
        repo.find_processing_by_title = AsyncMock(return_value=None)
        repo.find_active_by_title = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(docs_module, "DocRepo", lambda _db: repo)
        monkeypatch.setattr(docs_module.ParserFactory, "is_supported", lambda f: True)

        with pytest.raises(BizException) as exc_info:
            await upload_document(
                file=MagicMock(filename="active.txt"),
                category="regulation",
                summary="",
                auth=None,
                rate=None,
                db=db,
            )
        assert exc_info.value.code == ErrorCode.B0001
        assert "active" in exc_info.value.message
        repo.create.assert_not_called()


class TestDocRepoSweep:
    @pytest.mark.asyncio
    async def test_sweep_stale_processing(self):
        from app.repositories.doc_repo import DocRepo

        db = MagicMock()
        result = MagicMock()
        result.rowcount = 2
        db.execute = AsyncMock(return_value=result)
        repo = DocRepo(db)
        count = await repo.sweep_stale_processing(1800.0)
        assert count == 2


class TestBackgroundIngestCleanup:
    @pytest.mark.asyncio
    async def test_staging_file_removed_in_finally(self, tmp_path, monkeypatch):
        from app.services import ingest_service as ingest_mod

        staging = tmp_path / "job.txt"
        staging.write_bytes(b"x")
        doc_id = uuid.uuid4()

        async def fake_impl(*args, **kwargs):
            return None

        monkeypatch.setattr(ingest_mod, "_run_background_ingest_impl", fake_impl)

        await ingest_mod.run_background_ingest_job(
            doc_id, staging, "job.txt", "regulation", None
        )
        assert not staging.exists()
