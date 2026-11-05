"""T53 / 1.3：ES 补索引任务 spawn_tracked 强引用 + pending 集合确定性清理 单测。

验收判据：
- 降级入库派发的补索引任务在 spawn_tracked 强引用集合内（当前 loop 过滤，
  防 GC 逃逸后文档永久卡 processing）；
- backfill_es_index_task 成功路径 sadd → srem（pending 集合不残留）；
- 任务被取消（异常路径）时 finally 仍 srem（检索不漏召回）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ingest_service import (
    _BACKFILL_PENDING_KEY,
    IngestService,
    backfill_es_index_task,
)
from app.services.chunk_service import ChunkService
from app.settings import settings
from common.utils.spawn import _tracked_tasks, spawn_tracked


def _pending_tracked() -> list[asyncio.Task]:
    """当前运行 loop 的在途 tracked 任务（防用例级 loop 串扰）。"""
    loop = asyncio.get_running_loop()
    return [t for t in _tracked_tasks if t.get_loop() is loop and not t.done()]


class _MockDocRepo:
    def __init__(self):
        self.doc_id = uuid.uuid4()
        self.statuses: list[str] = []
        self.db = MagicMock()
        self.db.commit = AsyncMock()
        self.db.rollback = AsyncMock()

    async def create(self, **kwargs):
        return self.doc_id

    async def update_status(self, doc_id, status):
        self.statuses.append(status)

    async def clear_ingest_progress(self, doc_id):
        return None


class _MockChunkRepo:
    async def batch_create(self, entities):
        return None


class _MockMilvus:
    async def insert(self, vectors, metadata):
        return [str(uuid.uuid4()) for _ in vectors]


class _MockEmbed:
    async def batch_embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class _FailingES:
    async def index_docs(self, chunks):
        raise RuntimeError("es connection refused")


def _make_svc(doc_repo):
    return IngestService(
        doc_repo=doc_repo,
        chunk_repo=_MockChunkRepo(),
        milvus_client=_MockMilvus(),
        es_client=_FailingES(),
        embed_client=_MockEmbed(),
        chunk_service=ChunkService(chunk_size=100, chunk_overlap=20, min_length=1),
    )


class TestBackfillTracked:
    @pytest.mark.asyncio
    async def test_degraded_ingest_spawns_tracked_backfill(self, monkeypatch):
        """降级入库：补索引任务经 spawn_tracked 派发，在 tracked 强引用集合内。"""
        from app.parsers import ParserFactory

        monkeypatch.setattr(settings, "KB_ES_DEGRADED_KEEP_MILVUS", True)
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)

        class OkParser:
            async def extract_text(self, file_path):
                return "催收合规制度第一条。催收人员必须遵守法律法规。"

        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: OkParser())

        release = asyncio.Event()

        async def hanging_backfill(self, es_docs, doc_id):
            await release.wait()

        monkeypatch.setattr(IngestService, "_backfill_es_index", hanging_backfill)

        doc_repo = _MockDocRepo()
        result = await _make_svc(doc_repo).ingest(None, "test.png", "regulation")
        assert result["degraded"] is True
        assert doc_repo.statuses == ["active"]

        pending = _pending_tracked()
        assert pending, "补索引任务应在 spawn_tracked 强引用集合内（防 GC 逃逸）"
        release.set()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)
        assert _pending_tracked() == [], "任务 done 后应被确定性 discard"

    @pytest.mark.asyncio
    async def test_backfill_success_sadd_then_srem(self):
        """补索引成功：sadd 登记 → 索引成功 → finally srem（pending 不残留）。"""
        redis = AsyncMock()
        es = AsyncMock()
        doc_id = uuid.uuid4()

        task = spawn_tracked(
            backfill_es_index_task(es, redis, [{"chunk_id": "c1"}], doc_id)
        )
        await asyncio.wait_for(task, timeout=3)

        redis.sadd.assert_called_once_with(_BACKFILL_PENDING_KEY, str(doc_id))
        redis.srem.assert_called_once_with(_BACKFILL_PENDING_KEY, str(doc_id))

    @pytest.mark.asyncio
    async def test_backfill_cancelled_still_srem_pending(self):
        """任务取消（异常路径）：finally 仍执行 srem，pending 不残留（检索不漏召回）。"""
        redis = AsyncMock()
        es = AsyncMock()
        es.index_docs = AsyncMock(side_effect=RuntimeError("es down"))
        doc_id = uuid.uuid4()

        task = spawn_tracked(
            backfill_es_index_task(es, redis, [{"chunk_id": "c1"}], doc_id)
        )
        await asyncio.sleep(0.05)  # 让 sadd/expire 先执行
        redis.sadd.assert_called_once_with(_BACKFILL_PENDING_KEY, str(doc_id))

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        redis.srem.assert_called_once_with(_BACKFILL_PENDING_KEY, str(doc_id))