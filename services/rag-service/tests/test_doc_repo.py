"""DocRepo 列表查询 N+1 修复测试（rag-service-final-fix 5.11/5.12）。"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.doc_repo import DocRepo


class TestListByCategoryBatched:
    @pytest.mark.asyncio
    async def test_list_by_category_chunk_counts_batched(self):
        """列表查询用分组聚合一次取回全部 chunk 计数，不再逐行查询（无 N+1）。"""
        db = MagicMock()
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.title = "催收合规制度"
        doc.category = "regulation"
        doc.status = "active"
        doc.create_time = None

        # 查询顺序：① total 计数 ② 分页 docs ③ 分组聚合 chunk 计数
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        doc_result = MagicMock()
        doc_result.scalars.return_value.all.return_value = [doc]

        agg_result = MagicMock()
        agg_result.all.return_value = [(str(doc.id), 3)]

        db.execute = AsyncMock(side_effect=[count_result, doc_result, agg_result])
        repo = DocRepo(db)

        items, total = await repo.list_by_category(category="regulation", page=1, size=20)

        assert total == 1
        assert items[0]["id"] == str(doc.id)
        assert items[0]["chunks_count"] == 3
        # 三次查询：total 计数 + 分页 + 分组聚合；无逐行 N+1
        assert db.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_list_by_category_no_docs_zero_queries_extra(self):
        """无文档时不做聚合查询（分组聚合仅在 docs 非空时执行）。"""
        db = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        doc_result = MagicMock()
        doc_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[count_result, doc_result])
        repo = DocRepo(db)

        items, total = await repo.list_by_category(category="regulation", page=1, size=20)

        assert items == []
        assert total == 0
        assert db.execute.await_count == 2
