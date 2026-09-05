"""reindex 端到端测试（DD-03 §9.2 断言14）。

覆盖完整链路：执行 reindex → 旧 chunk / Milvus / ES 索引被删除 →
新分块向量/文本写入 → 文档状态恢复 active。
全部外部依赖（db / milvus / es / embed / chunk_repo）mock，不触碰真实服务。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.documents import reindex_document
from app.models.orm import KBDocument


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


class _MockMilvus:
    def __init__(self) -> None:
        self.delete_by_doc = AsyncMock()
        self.insert = AsyncMock(return_value=["v1", "v2"])


class _MockES:
    def __init__(self) -> None:
        self.delete_by_doc = AsyncMock()
        self.index_docs = AsyncMock()


class _MockEmbed:
    async def batch_embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


@pytest.mark.asyncio
async def test_reindex_full_flow():
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

    with patch(
        "app.api.documents.query_cache.invalidate_category", new=AsyncMock()
    ) as invalidate:
        with patch("app.api.documents.ChunkRepo") as chunk_repo_cls:
            chunk_repo = chunk_repo_cls.return_value
            chunk_repo.delete_by_doc = AsyncMock()
            chunk_repo.batch_create = AsyncMock()
            resp = await reindex_document(
                doc_id=str(uid), db=db, milvus=milvus, es=es, embed=embed
            )

    assert resp["code"] == "0"
    data = resp["data"]
    assert data["doc_id"] == str(uid)
    assert data["status"] == "active"
    assert data["chunks_count"] == 2
    assert data["assets_rebuilt"] is False

    # 旧索引删除：chunk 表 + Milvus + ES
    chunk_repo.delete_by_doc.assert_awaited_once()
    milvus.delete_by_doc.assert_awaited_once_with(str(uid))
    es.delete_by_doc.assert_awaited_once_with(str(uid))

    # 新索引写入：Milvus 向量 + ES 文本 + chunk 表
    milvus.insert.assert_awaited_once()
    es.index_docs.assert_awaited_once()
    chunk_repo.batch_create.assert_awaited_once()

    # 状态流转成功并提交，缓存按分类失效
    invalidate.assert_awaited_once()
    db.commit.assert_awaited_once()
