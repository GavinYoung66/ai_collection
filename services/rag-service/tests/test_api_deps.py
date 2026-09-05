"""deps 上传临时文件清理测试（rag-service-final-fix 5.1/5.2 隐患修复）。

异步入库后：API 层不再删除 staging 文件，由后台任务 finally 清理。
"""
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from app.api import documents as docs_module
from app.api.documents import upload_document
from common.config.settings import settings


class TestUploadStagingBehavior:
    @pytest.mark.asyncio
    async def test_upload_leaves_staging_for_background(self, monkeypatch, tmp_path):
        """异步入库：API commit 后 staging 文件保留，由后台任务删除。"""
        doc_id = uuid.uuid4()
        staging = tmp_path / f"{doc_id}.txt"
        staging.write_bytes(b"x" * 100)

        repo = MagicMock()
        repo.find_processing_by_title = AsyncMock(return_value=None)
        repo.find_active_by_title = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=doc_id)
        repo.update_ingest_stage = AsyncMock()

        async def fake_staging(file, did):
            return staging

        monkeypatch.setattr(docs_module, "DocRepo", lambda _db: repo)
        monkeypatch.setattr(docs_module, "save_upload_to_staging", fake_staging)
        monkeypatch.setattr(docs_module, "schedule_background_ingest", lambda *a, **k: None)
        monkeypatch.setattr(docs_module.ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)

        db = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        await upload_document(
            file=MagicMock(filename="x.txt"),
            category="regulation",
            summary="",
            auth=None, rate=None,
            db=db,
        )
        assert staging.exists()
