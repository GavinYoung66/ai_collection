"""资产清理测试（V5，tasks 11.8）。

覆盖：删除文档级联清理 kb_doc_assets 记录 + 本地资产目录 / 清理失败重试 2 次 + 告警不阻断主流程 /
remove_assets_dir 幂等 / reindex 先清后建（先资产清理再重建索引）。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.orm import KBDocument
from app.services.asset_service import remove_assets_dir
from app.services.ingest_service import _delete_assets_with_retry
from app.settings import settings


class TestDeleteAssetsWithRetry:
    @pytest.mark.asyncio
    async def test_success_cleans_records_and_dir(self, monkeypatch, tmp_path):
        """清理成功：kb_doc_assets 记录删除 + 本地资产目录删除，各调一次。"""
        monkeypatch.setattr(settings, "KB_ASSETS_DIR", str(tmp_path))
        doc_id = uuid.uuid4()
        doc_repo = MagicMock()
        doc_repo.delete_assets_by_doc = AsyncMock(return_value=(3, doc_id))
        with patch("app.services.ingest_service.remove_assets_dir") as m_rm:
            await _delete_assets_with_retry(str(doc_id), doc_repo)
        doc_repo.delete_assets_by_doc.assert_awaited_once_with(doc_id)
        m_rm.assert_called_once_with(doc_id)

    @pytest.mark.asyncio
    async def test_failure_retry_then_give_up(self):
        """清理失败：重试 2 次后告警放弃，不抛错（不阻断删除/reindex 主流程）。"""
        doc_id = uuid.uuid4()
        doc_repo = MagicMock()
        doc_repo.delete_assets_by_doc = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        with patch(
            "app.services.ingest_service.asyncio.sleep", new=AsyncMock()
        ) as m_sleep, \
            patch("app.services.ingest_service.remove_assets_dir") as m_rm:
            await _delete_assets_with_retry(str(doc_id), doc_repo)  # 不抛错
        assert doc_repo.delete_assets_by_doc.await_count == 3  # 初试 + 2 次重试
        assert m_sleep.await_count == 2
        m_rm.assert_not_called()


class TestRemoveAssetsDir:
    def test_idempotent(self, tmp_path, monkeypatch):
        """本地资产目录删除幂等（存在则删，不存在不报错）。"""
        monkeypatch.setattr(settings, "KB_ASSETS_DIR", str(tmp_path))
        doc_id = uuid.uuid4()
        target = tmp_path / str(doc_id)
        target.mkdir()
        (target / "1_a.png").write_bytes(b"x")
        remove_assets_dir(doc_id)
        assert not target.exists()
        remove_assets_dir(doc_id)  # 已不存在再删不报错


class TestDeleteDocumentCleanup:
    @pytest.mark.asyncio
    async def test_delete_route_cleans_assets(self, monkeypatch):
        """删除文档：ingest.delete_by_doc 级联清理 kb_doc_assets 记录 + 本地目录。"""
        from app.api import documents as documents_api

        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.category = "regulation"

        class MockMilvus:
            async def delete_by_doc(self, doc_id):
                pass

        class MockES:
            async def delete_by_doc(self, doc_id):
                pass

        class MockEmbed:
            async def batch_embed(self, texts):
                return [[0.1] * 1024 for _ in texts]

        async def _get_embed():
            return MockEmbed()

        monkeypatch.setattr(documents_api, "get_milvus_client", lambda: MockMilvus())
        monkeypatch.setattr(documents_api, "get_es_client", lambda: MockES())
        monkeypatch.setattr(documents_api, "get_embed_client", _get_embed)

        with patch(
            "app.services.ingest_service._delete_assets_with_retry",
            new=AsyncMock(),
        ) as m_clean:
            resp = await documents_api.delete_document(
                str(doc.id), db=_MockDocDB(doc)
            )
        assert resp["code"] == "0"
        m_clean.assert_awaited_once()


class TestReindexCleanup:
    @pytest.mark.asyncio
    async def test_reindex_cleans_before_rebuild(self, monkeypatch):
        """reindex：先级联清理旧资产，再重建双索引（先清后建）。"""
        from app.api.documents import reindex_document

        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)
        uid = uuid.uuid4()
        doc = KBDocument(
            id=uid,
            title="催收合规制度",
            category="regulation",
            content="第一条：催收人员不得使用威胁手段。\n\n第二条：逾期超过90天可申请协商还款。",
            status="active",
        )
        db = _mock_db(doc)
        milvus = _MockMilvus()
        es = _MockES()
        embed = _MockEmbed()
        order: list[str] = []

        orig_batch = embed.batch_embed

        async def batch_embed(texts):
            order.append("embed")
            return await orig_batch(texts)

        embed.batch_embed = batch_embed

        with patch(
            "app.api.documents._delete_assets_with_retry",
            new=AsyncMock(side_effect=lambda *a: order.append("assets")),
        ) as m_clean, \
            patch("app.api.documents.ChunkRepo") as chunk_repo_cls:
            chunk_repo = chunk_repo_cls.return_value
            chunk_repo.delete_by_doc = AsyncMock()
            chunk_repo.batch_create = AsyncMock()
            resp = await reindex_document(
                doc_id=str(uid), db=db, milvus=milvus, es=es, embed=embed
            )

        assert resp["code"] == "0"
        m_clean.assert_awaited_once()
        assert order == ["assets", "embed"]  # 先清旧资产，后重建索引


def _mock_db(doc: KBDocument) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = doc
    db.execute.return_value = result
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.add_all = MagicMock()
    return db


class _MockDocDB:
    def __init__(self, doc):
        self.doc = doc

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.doc
        result.scalars.return_value.all.return_value = []
        result.rowcount = 0
        return result

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _MockMilvus:
    def __init__(self):
        self.delete_by_doc = AsyncMock()
        self.insert = AsyncMock(return_value=["v1", "v2"])


class _MockES:
    def __init__(self):
        self.delete_by_doc = AsyncMock()
        self.index_docs = AsyncMock()


class _MockEmbed:
    async def batch_embed(self, texts):
        return [[0.1] * 1024 for _ in texts]
