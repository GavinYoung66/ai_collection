"""多模态解析器单元测试（§6.8 / §16.2）。

覆盖：
- ImageParser OCR 成功
- AudioParser ASR DashScope 成功
- 格式白名单外 → A3001
- OCR 3 连失败 → 解析失败
- 降级入库开关 true 时 Embedding 失败但写 ES 不 A3003
- 开关 false 时 A3003 与 V1 一致
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.parsers import ParserFactory
from app.parsers.audio_parser import AudioParser
from app.parsers.image_parser import ImageParser
from app.services.asr_providers.dashscope_provider import (
    DashScopeASRProvider,
    _asr_breakers,
)
from app.services.ocr_client import OCRClient
from app.settings import settings


class TestImageParser:
    @pytest.mark.asyncio
    async def test_ocr_success(self, tmp_path):
        """ImageParser 调用 OCRClient.recognize 返回文本。"""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"fake-image-bytes")
        ocr_client = MagicMock()
        ocr_client.recognize = AsyncMock(return_value="OCR 识别的制度截图内容")
        parser = ImageParser(ocr_client=ocr_client)
        text = await parser.extract_text(img)
        assert text == "OCR 识别的制度截图内容"
        ocr_client.recognize.assert_called_once()

    @pytest.mark.asyncio
    async def test_ocr_failure_raises(self, tmp_path):
        """OCR 3 连失败 → recognize 抛 RuntimeError，extract_text 传播异常。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"fake")
        ocr_client = MagicMock()
        ocr_client.recognize = AsyncMock(side_effect=RuntimeError("OCR circuit-break"))
        parser = ImageParser(ocr_client=ocr_client)
        with pytest.raises(RuntimeError, match="circuit-break"):
            await parser.extract_text(img)


class TestAudioParser:
    @pytest.mark.asyncio
    async def test_asr_success(self, tmp_path):
        """AudioParser 调用 ASRClient.transcribe 返回文本。"""
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake-audio-bytes")
        asr_client = MagicMock()
        asr_client.transcribe = AsyncMock(return_value="ASR 转写的通话录音文本")
        parser = AudioParser(asr_client=asr_client)
        text = await parser.extract_text(audio)
        assert text == "ASR 转写的通话录音文本"

    @pytest.mark.asyncio
    async def test_asr_failure_raises(self, tmp_path):
        """ASR 失败 → transcribe 抛异常，extract_text 传播。"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        asr_client = MagicMock()
        asr_client.transcribe = AsyncMock(side_effect=RuntimeError("ASR circuit-break"))
        parser = AudioParser(asr_client=asr_client)
        with pytest.raises(RuntimeError, match="circuit-break"):
            await parser.extract_text(audio)


class TestParserFactoryWhitelist:
    def test_unsupported_format_a3001(self):
        """格式白名单外后缀 → ParserFactory.is_supported 返回 False。"""
        assert ParserFactory.is_supported("doc.exe") is False
        assert ParserFactory.is_supported("file.gif") is False
        assert ParserFactory.is_supported("noext") is False

    def test_supported_formats(self):
        """支持的格式白名单校验（V5 增 .xlsx/.pptx/.amr/.wma）。"""
        for ext in [
            ".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg",
            ".mp3", ".wav", ".m4a", ".xlsx", ".pptx", ".amr", ".wma",
        ]:
            assert ParserFactory.is_supported(f"file{ext}") is True

    def test_unsupported_get_parser_raises(self, tmp_path):
        """get_parser 对不支持后缀抛 ValueError。"""
        f = tmp_path / "file.exe"
        f.write_bytes(b"x")
        with pytest.raises(ValueError, match="Unsupported"):
            ParserFactory.get_parser(f)


class TestIngestDegradedFallback:
    """§6.6 降级入库开关测试。"""

    @pytest.mark.asyncio
    async def test_degraded_keep_text_writes_es_not_a3003(self, monkeypatch):
        """KB_EMBED_DEGRADED_KEEP_TEXT=true 时 Embedding 失败 → 写 ES，不抛 A3003。"""
        from app.settings import settings
        monkeypatch.setattr(settings, "KB_EMBED_DEGRADED_KEEP_TEXT", True)
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)

        from app.services.ingest_service import IngestService

        doc_repo = MagicMock()
        doc_repo.create = AsyncMock(return_value="doc-uuid")
        doc_repo.update_status = AsyncMock()
        chunk_repo = MagicMock()
        chunk_repo.batch_create = AsyncMock()
        milvus = MagicMock()
        es = MagicMock()
        es.index_docs = AsyncMock()
        embed = MagicMock()
        embed.batch_embed = AsyncMock(side_effect=RuntimeError("embed down"))

        svc = IngestService(doc_repo, chunk_repo, milvus, es, embed)
        # mock parser：extract_text 返回固定文本，不读真实文件
        mock_parser = MagicMock()
        mock_parser.extract_text = AsyncMock(return_value="raw text")
        with patch("app.services.ingest_service.ParserFactory") as mock_pf, \
             patch.object(svc.chunk_service, "clean", return_value="cleaned"), \
             patch.object(svc.chunk_service, "split", return_value=[
                 {"id": "", "doc_id": "", "chunk_index": 0, "content": "c", "category": "regulation"}
             ]):
            mock_pf.is_supported.return_value = True
            mock_pf.get_parser.return_value = mock_parser
            result = await svc.ingest(Path("test.txt"), "test.txt", "regulation")
        assert result["status"] == "active"
        assert result["degraded"] is True
        es.index_docs.assert_called_once()
        milvus.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_switch_off_raises_a3003(self, monkeypatch):
        """KB_EMBED_DEGRADED_KEEP_TEXT=false 时 Embedding 失败 → 抛 A3003（V1 一致）。"""
        from app.settings import settings
        monkeypatch.setattr(settings, "KB_EMBED_DEGRADED_KEEP_TEXT", False)
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)

        from app.services.ingest_service import IngestService
        from common.errors.exceptions import BizException
        from common.errors.codes import ErrorCode

        doc_repo = MagicMock()
        doc_repo.create = AsyncMock(return_value="doc-uuid")
        doc_repo.update_status = AsyncMock()
        chunk_repo = MagicMock()
        milvus = MagicMock()
        es = MagicMock()
        embed = MagicMock()
        embed.batch_embed = AsyncMock(side_effect=RuntimeError("embed down"))

        svc = IngestService(doc_repo, chunk_repo, milvus, es, embed)
        mock_parser = MagicMock()
        mock_parser.extract_text = AsyncMock(return_value="raw text")
        with patch("app.services.ingest_service.ParserFactory") as mock_pf, \
             patch.object(svc.chunk_service, "clean", return_value="cleaned"), \
             patch.object(svc.chunk_service, "split", return_value=[
                 {"id": "", "doc_id": "", "chunk_index": 0, "content": "c", "category": "regulation"}
             ]):
            mock_pf.is_supported.return_value = True
            mock_pf.get_parser.return_value = mock_parser
            with pytest.raises(BizException) as exc_info:
                await svc.ingest(Path("test.txt"), "test.txt", "regulation")
            assert exc_info.value.code == ErrorCode.A3003


async def _trip_open(breaker) -> None:
    for _ in range(breaker.fail_max):
        await breaker.record_failure(RuntimeError("down"))


class TestOcrCircuitBreaker:
    @pytest.mark.asyncio
    async def test_ocr_breaker_open_raises_not_empty_string(self, tmp_path):
        """breaker open → 短路 HTTP 并抛错，不得返回空串（避免误走 A3002）。"""
        ocr = OCRClient()
        await _trip_open(ocr._breaker)

        async def boom(*args, **kwargs):
            raise AssertionError("OCR HTTP must be skipped when breaker is open")

        ocr._get_client = boom  # type: ignore[method-assign]
        img = tmp_path / "t.png"
        img.write_bytes(b"fake")
        parser = ImageParser(ocr_client=ocr)
        with pytest.raises(RuntimeError, match="circuit-break"):
            await parser.extract_text(img)

    @pytest.mark.asyncio
    async def test_ocr_success_empty_text_is_blank_image(self):
        """HTTP 200 空文本是真空白图，返回 "" 且不记熔断失败。"""
        ocr = OCRClient()

        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"text": ""}

        class FakeClient:
            is_closed = False

            async def post(self, *args, **kwargs):
                return FakeResp()

        ocr._client = FakeClient()  # type: ignore[assignment]
        text = await ocr.recognize(b"white", "blank.png")
        assert text == ""
        assert ocr._breaker.state == "closed"


class TestAsrCircuitBreaker:
    @pytest.mark.asyncio
    async def test_dashscope_breaker_open_raises(self, monkeypatch):
        """DashScope breaker open → 转写失败标记，不调 HTTP。"""
        _asr_breakers.clear()
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "test-key")
        provider = DashScopeASRProvider()
        await _trip_open(provider._breaker)

        async def boom(*args, **kwargs):
            raise AssertionError("ASR HTTP must be skipped when breaker is open")

        provider._get_client = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="circuit-break"):
            await provider.transcribe(b"fake-audio", "mp3")
        _asr_breakers.clear()

