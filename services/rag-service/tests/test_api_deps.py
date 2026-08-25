"""deps 上传临时文件清理测试（rag-service-final-fix 5.1/5.2 隐患修复）。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import documents as docs_module
from app.api.documents import upload_document
from common.config.settings import settings
from common.errors.exceptions import BizException


class TestUploadTempFileCleanedUp:
    def _patch_upload_deps(self, monkeypatch, tmp_path):
        """接管 save_upload_file → 在 tmp_path 创建临时文件并记录路径。"""
        captured: dict = {}

        async def fake_save(file):
            path = tmp_path / "upload_test.txt"
            path.write_bytes(b"x" * 100)
            captured["path"] = path
            return path

        monkeypatch.setattr(docs_module, "save_upload_file", fake_save)

        class MockIngest:
            async def ingest(self, file_path, filename, category, created_by=None, summary=None):
                return {"doc_id": "doc-1", "chunks_count": 1, "status": "active"}

        monkeypatch.setattr(
            docs_module, "_get_ingest_service", lambda *a, **k: MockIngest()
        )
        monkeypatch.setattr(docs_module.ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)
        return captured

    @pytest.mark.asyncio
    async def test_upload_temp_file_cleaned_up(self, monkeypatch, tmp_path):
        """上传成功后临时文件被 finally 清理，目录无残留。"""
        captured = self._patch_upload_deps(monkeypatch, tmp_path)
        await upload_document(
            file=MagicMock(filename="x.txt"),
            category="regulation",
            summary="",
            auth=None, rate=None,
            db=AsyncMock(), milvus=MagicMock(), es=MagicMock(), embed=MagicMock(),
        )
        assert not captured["path"].exists()

    @pytest.mark.asyncio
    async def test_upload_failure_also_cleans_up(self, monkeypatch, tmp_path):
        """上传/入库失败时临时文件同样被清理（finally 兜底）。"""
        captured = self._patch_upload_deps(monkeypatch, tmp_path)

        class FailingIngest:
            async def ingest(self, file_path, filename, category, created_by=None, summary=None):
                raise BizException("A3001", "文档解析失败")

        monkeypatch.setattr(
            docs_module, "_get_ingest_service", lambda *a, **k: FailingIngest()
        )
        with pytest.raises(BizException):
            await upload_document(
                file=MagicMock(filename="x.txt"),
                category="regulation",
                summary="",
                auth=None, rate=None,
                db=AsyncMock(), milvus=MagicMock(), es=MagicMock(), embed=MagicMock(),
            )
        assert not captured["path"].exists()
