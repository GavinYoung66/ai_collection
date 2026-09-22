"""入库降级单元测试（19.1 OCR/ASR 解析失败降级、19.4 ES 入库失败降级）。

覆盖：
- OCR/ASR 解析失败：开关开启 → 占位入库 status=failed + degraded=true；开关关闭 → A3001
- ES 入库失败：开关开启 → 保留 Milvus + status=active + degraded=true；开关关闭 → A3003
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.parsers import ParserFactory
from app.services.chunk_service import ChunkService
from app.services.ingest_service import IngestService
from app.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


class MockDocRepo:
    def __init__(self):
        self.doc_id = uuid.uuid4()
        self.statuses = []
        self.db = MagicMock()
        self.db.commit = AsyncMock()
        self.db.rollback = AsyncMock()

    async def create(self, **kwargs):
        return self.doc_id

    async def update_status(self, doc_id, status):
        self.statuses.append(status)


class MockChunkRepo:
    def __init__(self):
        self.entities = None

    async def batch_create(self, entities):
        self.entities = entities


class MockMilvus:
    async def insert(self, vectors, metadata):
        return [str(uuid.uuid4()) for _ in vectors]


class MockEmbed:
    async def batch_embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class FailingES:
    async def index_docs(self, chunks):
        raise RuntimeError("es connection refused")


def _make_svc(doc_repo, chunk_repo, milvus, es, embed):
    return IngestService(
        doc_repo=doc_repo,
        chunk_repo=chunk_repo,
        milvus_client=milvus,
        es_client=es,
        embed_client=embed,
        chunk_service=ChunkService(chunk_size=100, chunk_overlap=20, min_length=1),
    )


class TestParseFailureDegradation:
    def _mock_failing_parser(self, monkeypatch):
        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)

        class FailingParser:
            async def extract_text(self, file_path):
                raise RuntimeError("ocr service down")

        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: FailingParser())

    @pytest.mark.asyncio
    async def test_parse_failure_degraded_on(self, monkeypatch):
        """开关开启 → 解析失败占位入库 status=failed + degraded=true。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", True)
        self._mock_failing_parser(monkeypatch)
        doc_repo = MockDocRepo()
        svc = _make_svc(doc_repo, MockChunkRepo(), MockMilvus(), FailingES(), MockEmbed())
        result = await svc.ingest(None, "test.png", "regulation")
        assert result["degraded"] is True
        assert result["status"] == "failed"
        assert doc_repo.statuses == ["failed"]

    @pytest.mark.asyncio
    async def test_parse_failure_switch_off_raises_a3001(self, monkeypatch):
        """开关关闭 → 解析失败保持 V2 行为，抛 A3001。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", False)
        self._mock_failing_parser(monkeypatch)
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), FailingES(), MockEmbed())
        with pytest.raises(BizException) as exc_info:
            await svc.ingest(None, "test.png", "regulation")
        assert exc_info.value.code == ErrorCode.A3001


class TestOcrAsrBreakerIngest:
    @pytest.mark.asyncio
    async def test_ocr_breaker_open_raises_a3001(self, monkeypatch, tmp_path):
        """OCR breaker open + 开关关 → A3001，不 500。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", False)
        from app.services.ocr_client import OCRClient
        from app.parsers.image_parser import ImageParser

        ocr = OCRClient()
        for _ in range(ocr._breaker.fail_max):
            await ocr._breaker.record_failure(RuntimeError("down"))
        img = tmp_path / "test.png"
        img.write_bytes(b"x")
        parser = ImageParser(ocr_client=ocr)
        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: parser)
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), FailingES(), MockEmbed())
        with pytest.raises(BizException) as exc_info:
            await svc.ingest(img, "test.png", "regulation")
        assert exc_info.value.code == ErrorCode.A3001

    @pytest.mark.asyncio
    async def test_ocr_breaker_open_placeholder(self, monkeypatch, tmp_path):
        """OCR breaker open + 占位开关 → status=failed degraded，不 500。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", True)
        from app.services.ocr_client import OCRClient
        from app.parsers.image_parser import ImageParser

        ocr = OCRClient()
        for _ in range(ocr._breaker.fail_max):
            await ocr._breaker.record_failure(RuntimeError("down"))
        img = tmp_path / "test.png"
        img.write_bytes(b"x")
        parser = ImageParser(ocr_client=ocr)
        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: parser)
        doc_repo = MockDocRepo()
        svc = _make_svc(doc_repo, MockChunkRepo(), MockMilvus(), FailingES(), MockEmbed())
        result = await svc.ingest(img, "test.png", "regulation")
        assert result["degraded"] is True
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_blank_ocr_text_raises_a3002(self, monkeypatch):
        """真空白图识别空文本 → A3002，不是熔断 A3001。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", False)
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)

        class BlankParser:
            async def extract_text(self, file_path):
                return ""

        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: BlankParser())
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), FailingES(), MockEmbed())
        with pytest.raises(BizException) as exc_info:
            await svc.ingest(None, "blank.png", "regulation")
        assert exc_info.value.code == ErrorCode.A3002

    @pytest.mark.asyncio
    async def test_asr_breaker_open_raises_a3001(self, monkeypatch, tmp_path):
        """ASR breaker open + 开关关 → A3001，不阻塞入库。"""
        monkeypatch.setattr(settings, "KB_OCR_ASR_DEGRADED_KEEP_TEXT", False)

        class CircuitAsr:
            async def transcribe(self, file_bytes, filename):
                raise RuntimeError("ASR service is in circuit-break state")

        from app.parsers.audio_parser import AudioParser

        audio = tmp_path / "call.mp3"
        audio.write_bytes(b"fake-audio")
        parser = AudioParser(asr_client=CircuitAsr())
        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)
        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: parser)
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), FailingES(), MockEmbed())
        with pytest.raises(BizException) as exc_info:
            await svc.ingest(audio, "call.mp3", "case")
        assert exc_info.value.code == ErrorCode.A3001


class TestESFailureDegradation:
    def _mock_ok_parser(self, monkeypatch):
        monkeypatch.setattr(ParserFactory, "is_supported", lambda f: True)

        class OkParser:
            async def extract_text(self, file_path):
                return "催收合规制度第一条。催收人员必须遵守法律法规。"

        monkeypatch.setattr(ParserFactory, "get_parser", lambda fp: OkParser())

    @pytest.mark.asyncio
    async def test_es_failure_degraded_on(self, monkeypatch):
        """开关开启 → ES 失败保留 Milvus + status=active + degraded=true。"""
        monkeypatch.setattr(settings, "KB_ES_DEGRADED_KEEP_MILVUS", True)
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(IngestService, "_backfill_es_index", AsyncMock())
        self._mock_ok_parser(monkeypatch)
        doc_repo = MockDocRepo()
        chunk_repo = MockChunkRepo()
        svc = _make_svc(doc_repo, chunk_repo, MockMilvus(), FailingES(), MockEmbed())
        result = await svc.ingest(None, "test.png", "regulation")
        assert result["degraded"] is True
        assert result["status"] == "active"
        assert doc_repo.statuses == ["active"]
        assert chunk_repo.entities is not None
        assert len(chunk_repo.entities) == 1

    @pytest.mark.asyncio
    async def test_es_failure_switch_off_raises_a3003(self, monkeypatch):
        """开关关闭 → ES 失败保持 V2 行为，抛 A3003。"""
        monkeypatch.setattr(settings, "KB_ES_DEGRADED_KEEP_MILVUS", False)
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        self._mock_ok_parser(monkeypatch)
        svc = _make_svc(MockDocRepo(), MockChunkRepo(), MockMilvus(), FailingES(), MockEmbed())
        with pytest.raises(BizException) as exc_info:
            await svc.ingest(None, "test.png", "regulation")
        assert exc_info.value.code == ErrorCode.A3003
