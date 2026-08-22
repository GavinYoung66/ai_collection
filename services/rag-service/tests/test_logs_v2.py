"""LogService V2 单元测试：VARCHAR 转换 + 5 新字段传递。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.log_service import LogService


@pytest.fixture
def mock_db():
    """Mock AsyncSession：捕获 add 的 KBSearchLog 实例。"""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def base_kwargs():
    """LogService.log 最小必填参数。"""
    return dict(
        trace_id="trace-test-001",
        query="逾期协商还款",
        category="regulation",
        dense_hit_count=5,
        sparse_hit_count=3,
        fused_count=4,
        reranked_count=4,
        max_rerank_score=0.85,
        hit_doc_ids=["doc-1", "doc-2"],
        hit_chunk_ids=["chunk-1", "chunk-2"],
        latency_ms=120,
        degraded=False,
        is_fallback=False,
    )


@pytest.mark.asyncio
async def test_degraded_true_stores_varchar_1(mock_db, base_kwargs):
    """degraded=True → KBSearchLog.degraded 存 '1'（VARCHAR 非布尔）。"""
    base_kwargs["degraded"] = True
    svc = LogService(mock_db)
    await svc.log(**base_kwargs)
    entry = mock_db.add.call_args[0][0]
    assert entry.degraded == "1"
    assert entry.is_fallback == "0"


@pytest.mark.asyncio
async def test_degraded_false_stores_varchar_0(mock_db, base_kwargs):
    """degraded=False → KBSearchLog.degraded 存 '0'。"""
    svc = LogService(mock_db)
    await svc.log(**base_kwargs)
    entry = mock_db.add.call_args[0][0]
    assert entry.degraded == "0"
    assert entry.is_fallback == "0"


@pytest.mark.asyncio
async def test_is_fallback_true_stores_varchar_1(mock_db, base_kwargs):
    """is_fallback=True → KBSearchLog.is_fallback 存 '1'。"""
    base_kwargs["is_fallback"] = True
    svc = LogService(mock_db)
    await svc.log(**base_kwargs)
    entry = mock_db.add.call_args[0][0]
    assert entry.is_fallback == "1"


@pytest.mark.asyncio
async def test_cache_hit_field(mock_db, base_kwargs):
    """cache_hit='1' 正确传递到 KBSearchLog。"""
    base_kwargs["cache_hit"] = "1"
    svc = LogService(mock_db)
    await svc.log(**base_kwargs)
    entry = mock_db.add.call_args[0][0]
    assert entry.cache_hit == "1"


@pytest.mark.asyncio
async def test_v2_new_fields_all_present(mock_db, base_kwargs):
    """V2 新增 5 字段（query_original/query_rewritten/intent_tag/session_id）全部写入。"""
    base_kwargs.update(
        query_original="逾期协商还款",
        query_rewritten="逾期 协商 还款方案",
        intent_tag="negotiate",
        session_id="01JZTESTSESSIONID0001",
    )
    svc = LogService(mock_db)
    await svc.log(**base_kwargs)
    entry = mock_db.add.call_args[0][0]
    assert entry.query_original == "逾期协商还款"
    assert entry.query_rewritten == "逾期 协商 还款方案"
    assert entry.intent_tag == "negotiate"
    assert entry.session_id == "01JZTESTSESSIONID0001"
    assert entry.cache_hit == "0"  # 默认值


@pytest.mark.asyncio
async def test_v2_fields_default_none(mock_db, base_kwargs):
    """V2 新增字段缺省时为 None（不传 → 默认）。"""
    svc = LogService(mock_db)
    await svc.log(**base_kwargs)
    entry = mock_db.add.call_args[0][0]
    assert entry.query_original is None
    assert entry.query_rewritten is None
    assert entry.intent_tag is None
    assert entry.session_id is None
    assert entry.cache_hit == "0"
