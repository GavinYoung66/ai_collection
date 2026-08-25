"""入库解析超时测试（rag-service-final-fix 2.5：KB_PARSE_TIMEOUT 超时走既有失败路径）。

另含组 5 隐患修复测试：5.8 删除重试、5.10 补索引持久化。
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError
from structlog.testing import capture_logs

from app.parsers import ParserFactory
from app.services.chunk_service import ChunkService
from app.services.ingest_service import IngestService
from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


class MockDocRepo:
    def __init__(self):
        self.doc_id = uuid.uuid4()
        self.statuses = []
        self.soft_deleted = []

    async def create(self, **kwargs):
        return self.doc_id

    async def update_status(self, doc_id, status):
        self.statuses.append(status)

    async def soft_delete(self, doc_id):
        self.soft_deleted.append(doc_id)


class MockChunkRepo:
    def __init__(self):
        self.deleted = []

    async def batch_create(self, entities):
        pass

    async def delete_by_doc(self, doc_id):
        self.deleted.append(doc_id)


class MockMilvus:
    async def insert(self, vectors, metadata):
        return [str(uuid.uuid4()) for _ in vectors]

    async def delete_by_doc(self, doc_id):
        pass


class MockEmbed:
    async def batch_embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class MockES:
    def __init__(self):
        self.deleted = []

    async def index_docs(self, chunks):
        pass

    async def delete_by_doc(self, doc_id):
        self.deleted.append(doc_id)


def _make_svc(doc_repo, chunk_repo, milvus, es, embed, redis=None):
    return IngestService(
        doc_repo=doc_repo,
        chunk_repo=chunk_repo,
        milvus_client=milvus,
        es_client=es,
        embed_client=embed,
        chunk_service=ChunkService(chunk_size=100, chunk_overlap=20, min_length=1),
        redis=redis,
    )


class TestParseTimeout:
    def _mock_slow_parser(self, monkeypatch, delay: float):
        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)

        class SlowParser:
            async def extract_text(self, file_path):
                await asyncio.sleep(delay)
                return "解析完成后的文本"

        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: SlowParser())

    @pytest.mark.asyncio
    async def test_parse_timeout_returns_a3001(self, monkeypatch):
        """解析超时（降级开关关）→ 按既有解析失败路径抛 A3001。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", False)
        monkeypatch.setattr(settings, "KB_PARSE_TIMEOUT", 0.05)
        self._mock_slow_parser(monkeypatch, delay=0.5)
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), MockES(), MockEmbed())
        with pytest.raises(BizException) as exc_info:
            await svc.ingest(None, "test.pdf", "regulation")
        assert exc_info.value.code == ErrorCode.A3001
        assert "解析" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_parse_timeout_degraded_keep_text_branch(self, monkeypatch):
        """解析超时（降级开关开）→ 走既有降级分支：占位入库 status=failed + degraded=true。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", True)
        monkeypatch.setattr(settings, "KB_PARSE_TIMEOUT", 0.05)
        self._mock_slow_parser(monkeypatch, delay=0.5)
        doc_repo = MockDocRepo()
        svc = _make_svc(doc_repo, MockChunkRepo(), MockMilvus(), MockES(), MockEmbed())
        result = await svc.ingest(None, "test.pdf", "regulation")
        assert result["degraded"] is True
        assert result["status"] == "failed"
        assert doc_repo.statuses == ["failed"]


class FlakyMilvus:
    """delete_by_doc 前 n 次失败、之后成功的 Mock。"""

    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.delete_calls = 0

    async def insert(self, vectors, metadata):
        return [str(uuid.uuid4()) for _ in vectors]

    async def delete_by_doc(self, doc_id):
        self.delete_calls += 1
        if self.delete_calls <= self.fail_times:
            raise RuntimeError("milvus delete boom")


class TestDeleteRetry:
    """5.8 删除外部索引失败重试 2 次（1s/3s），仍失败告警日志；PG 软删除不受影响。"""

    @pytest.mark.asyncio
    async def test_delete_retry_then_success(self, monkeypatch):
        """Milvus 前两次失败、第三次成功 → 不抛异常，ES 与 PG 软删除正常执行。"""
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        doc_repo = MockDocRepo()
        chunk_repo = MockChunkRepo()
        milvus = FlakyMilvus(fail_times=2)
        es = MockES()
        svc = _make_svc(doc_repo, chunk_repo, milvus, es, MockEmbed())
        await svc.delete_by_doc(str(doc_repo.doc_id))
        assert milvus.delete_calls == 3
        assert es.deleted == [str(doc_repo.doc_id)]
        assert chunk_repo.deleted == [doc_repo.doc_id]
        assert doc_repo.soft_deleted == [doc_repo.doc_id]

    @pytest.mark.asyncio
    async def test_delete_retry_fail_logs_alert(self, monkeypatch):
        """Milvus 始终失败 → 记录 delete_retry_give_up 告警；PG 软删除仍执行、不抛异常。"""
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        doc_repo = MockDocRepo()
        chunk_repo = MockChunkRepo()
        milvus = FlakyMilvus(fail_times=99)
        es = MockES()
        svc = _make_svc(doc_repo, chunk_repo, milvus, es, MockEmbed())
        with capture_logs() as cap:
            await svc.delete_by_doc(str(doc_repo.doc_id))
        assert any(e["event"] == "delete_retry_give_up" for e in cap)
        assert milvus.delete_calls == 3
        assert doc_repo.soft_deleted == [doc_repo.doc_id]


class TestBackfillPending:
    """5.10 补索引待补集合持久化：写入 rag:backfill:pending（TTL 30min），完成/放弃后移除。"""

    def _redis_mock(self):
        redis = MagicMock()
        redis.sadd = AsyncMock(return_value=1)
        redis.expire = AsyncMock(return_value=True)
        redis.srem = AsyncMock(return_value=1)
        return redis

    @pytest.mark.asyncio
    async def test_backfill_pending_persisted_after_restart(self, monkeypatch):
        """ES 补索引成功：先 sadd+expire 持久化，成功后 srem 移除。"""
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        redis = self._redis_mock()
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), MockES(), MockEmbed(), redis=redis)
        doc_id = uuid.uuid4()
        await svc._backfill_es_index([{"chunk_id": "c1", "content": "x"}], doc_id)
        redis.sadd.assert_awaited_once_with("rag:backfill:pending", str(doc_id))
        redis.expire.assert_awaited_once_with("rag:backfill:pending", 30 * 60)
        redis.srem.assert_awaited_once_with("rag:backfill:pending", str(doc_id))

    @pytest.mark.asyncio
    async def test_backfill_pending_redis_error_no_block(self, monkeypatch):
        """Redis 写入失败仅 warning，不阻断补索引主流程（含 finally 清理容错）。"""
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        redis = self._redis_mock()
        redis.sadd = AsyncMock(side_effect=RedisError("redis down"))
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), MockES(), MockEmbed(), redis=redis)
        doc_id = uuid.uuid4()
        await svc._backfill_es_index([{"chunk_id": "c1", "content": "x"}], doc_id)
        assert redis.srem.await_count <= 1
