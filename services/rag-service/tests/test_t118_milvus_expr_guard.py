"""T118（豁免规格）：milvus expr 入口白名单 + 形态守护。

修复以入口集中白名单断言 + 注释固化为形态（不扩大到全量参数化重构）：
非法 expr 片段在入口被拒；全部 expr 调用的插值来源在白名单/int64 收敛。
"""
from __future__ import annotations

import inspect

import pytest

from app.vectorstore.milvus_client import (
    _MILVUS_CATEGORIES,
    MilvusClient,
)


class TestT118MilvusExprGuard:
    @pytest.mark.asyncio
    async def test_illegal_category_rejected_before_thread(self):
        """非法 expr 片段在入口被拒（spec 场景：不连 Milvus、不进线程池）。"""
        client = MilvusClient(host="localhost", port=19530)
        with pytest.raises(ValueError, match="illegal milvus expr category"):
            await client.search([0.1], category="x/../y")
        with pytest.raises(ValueError, match="illegal milvus expr category"):
            await client.search([0.1], category='admin" or "1"=="1')

    @pytest.mark.asyncio
    async def test_legal_category_passes_gate(self):
        """合法 category 通过白名单门：白名单拒绝（ValueError）不发生——
        后续可能因未连接 Milvus 抛连接错，但不属于入口拒绝。"""
        client = MilvusClient(host="localhost", port=19530)
        try:
            await client.search([0.1], category="regulation")
            rejected = None
        except ValueError as exc:
            rejected = exc if "illegal milvus expr" in str(exc) else None
        except Exception:
            rejected = None  # 连接错等非白名单拒绝
        assert rejected is None

    def test_whitelist_aligned_with_search_request_pattern(self):
        """白名单与 SearchRequest pydantic pattern 锚定（两处 MUST 同步）。"""
        from app.models.search import SearchRequest

        for category in _MILVUS_CATEGORIES:
            SearchRequest(query="q", category=category)  # 不抛 = pattern 接受

    def test_expr_sources_guarded(self):
        """形态守护：全部 expr 调用的插值来源收敛——search 走白名单断言，
        delete 的 doc_id 经 uuid_to_int64 收敛为 int64（非字符串插值）。"""
        search_src = inspect.getsource(MilvusClient.search)
        assert "_MILVUS_CATEGORIES" in search_src
        delete_src = inspect.getsource(MilvusClient.delete_by_doc)
        assert "uuid_to_int64(doc_id)" in delete_src