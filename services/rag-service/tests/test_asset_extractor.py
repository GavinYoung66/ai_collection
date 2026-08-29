"""内嵌资产抽取单元测试（V5，design D4/D5；tasks 11.3）。

覆盖：PDF/DOCX/PPTX 内嵌图片抽取（含矢量图渲染位图路径）/ 图片 OCR 文本 chunk 入库
（mock ocr-service）/ kb_doc_assets 元数据落表 / 单张失败不影响正文 / 开关关闭零资产。
"""
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.parsers.asset_extractor import AssetExtractor, assets_enabled
from app.services.asset_service import AssetService
from common.config.settings import settings


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(buf, format="PNG")
    return buf.getvalue()


class TestAssetExtractor:
    @pytest.mark.asyncio
    async def test_unsupported_ext_returns_empty(self, tmp_path):
        """非 PDF/DOCX/PPTX 后缀 → 空资产列表。"""
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert await AssetExtractor().extract(f) == []

    @pytest.mark.asyncio
    async def test_pdf_bitmap_image_extracted(self, tmp_path):
        """PDF 位图图片抽取（get_images → Pixmap → 缩放 PNG）。"""
        class FakePixmap:
            def __init__(self, *a, **k):
                self.n = 3

            def tobytes(self, fmt="png"):
                return _png_bytes()

        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-fake")
        with patch("fitz.open") as mo, \
             patch("fitz.Pixmap", FakePixmap), \
             patch("fitz.csRGB", "rgb"), \
             patch("pdfplumber.open") as mp:
            page = MagicMock()
            page.number = 0
            page.rect = MagicMock()
            page.rect.width = 800
            page.rect.height = 600
            page.get_images.return_value = [(0,)]
            page.get_drawings.return_value = []
            doc = mo.return_value
            doc.__enter__.return_value = [page]
            pdf = mp.return_value.__enter__.return_value
            pdf.pages = []
            assets = AssetExtractor()._extract_pdf(f)
        assert len(assets) == 1
        assert assets[0]["type"] == "image" and assets[0]["page"] == 1
        assert assets[0]["binary"]

    @pytest.mark.asyncio
    async def test_pdf_vector_render_path(self, tmp_path):
        """矢量图经渲染接口转位图路径（get_drawings → clip → get_pixmap）。"""
        class FakePixmap:
            def __init__(self, *a, **k):
                self.n = 3

            def tobytes(self, fmt="png"):
                return _png_bytes()

        class FakeRect:
            def __init__(self, x0, y0, x1, y1):
                self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
                self.width, self.height = x1 - x0, y1 - y0

        f = tmp_path / "vec.pdf"
        f.write_bytes(b"%PDF-fake")
        with patch("fitz.open") as mo, \
             patch("fitz.Pixmap", FakePixmap), \
             patch("fitz.Rect", FakeRect), \
             patch("fitz.Matrix", lambda *a: "m"), \
             patch("pdfplumber.open") as mp:
            page = MagicMock()
            page.number = 0
            page.rect = FakeRect(0, 0, 800, 600)
            page.get_images.return_value = []
            page.get_drawings.return_value = [{"rect": FakeRect(100, 100, 200, 200)}]
            page.get_pixmap.return_value = FakePixmap()
            doc = mo.return_value
            doc.__enter__.return_value = [page]
            pdf = mp.return_value.__enter__.return_value
            pdf.pages = []
            assets = AssetExtractor()._extract_pdf(f)
        assert len(assets) == 1 and assets[0]["type"] == "image"

    def test_docx_inline_shape_extracted(self, tmp_path):
        """DOCX inline_shapes 图片抽取（related_parts 二进制）。"""
        f = tmp_path / "a.docx"
        f.write_bytes(b"fake")
        with patch("docx.Document") as mo:
            doc = mo.return_value
            shape = MagicMock()
            blip = shape._inline.graphic.graphicData.pic.blipFill.blip
            blip.embed = "rId1"
            part = MagicMock()
            part.blob = _png_bytes()
            doc.part.related_parts.__getitem__.return_value = part
            doc.inline_shapes = [shape]
            doc.tables = []
            assets = AssetExtractor()._extract_docx(f)
        assert len(assets) == 1 and assets[0]["type"] == "image" and assets[0]["page"] == 0

    def test_pptx_picture_extracted(self, tmp_path):
        """PPTX picture shape 图片抽取（page=i）。"""
        f = tmp_path / "a.pptx"
        f.write_bytes(b"fake")
        with patch("pptx.Presentation") as mo, \
             patch("pptx.enum.shapes.MSO_SHAPE_TYPE") as mtype:
            shape = MagicMock()
            shape.shape_type = mtype.PICTURE
            shape.image.blob = _png_bytes()
            slide = MagicMock()
            slide.shapes = [shape]
            prs = mo.return_value
            prs.slides = [slide]
            assets = AssetExtractor()._extract_pptx(f)
        assert len(assets) == 1 and assets[0]["page"] == 1

    def test_assets_disabled_when_switches_off(self, monkeypatch):
        """图片/表格开关全关 → assets_enabled() 为 False（零资产）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        assert assets_enabled() is False

    def test_assets_enabled_when_any_switch_on(self, monkeypatch):
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        assert assets_enabled() is True


class TestAssetServiceIngest:
    def _svc(self, ocr=None):
        """注入 AsyncMock 化的 batch_create（服务内 await，普通 MagicMock 会 TypeError）。"""
        chunk_repo = MagicMock()
        chunk_repo.batch_create = AsyncMock()
        chunk_repo.next_chunk_index = AsyncMock(return_value=1)
        asset_repo = MagicMock()
        asset_repo.batch_create = AsyncMock()
        return AssetService(
            doc_repo=MagicMock(),
            chunk_repo=chunk_repo,
            asset_repo=asset_repo,
            milvus_client=MagicMock(),
            es_client=MagicMock(),
            embed_client=MagicMock(),
            ocr_client=ocr,
        )

    @pytest.mark.asyncio
    async def test_image_ocr_chunk_and_metadata(self, monkeypatch):
        """图片：OCR 文本以 chunk 双索引入库（[图片] 前缀）+ kb_doc_assets 元数据落表。"""
        import uuid

        monkeypatch.setattr(settings, "KB_ASSETS_DIR", "/tmp")
        ocr = MagicMock()
        ocr.recognize = AsyncMock(return_value="识别出的制度文本")
        svc = self._svc(ocr=ocr)
        svc.embed.batch_embed = AsyncMock(return_value=[[0.1] * 4])
        svc.milvus.insert = AsyncMock(return_value=["vid1"])
        svc.es.index_docs = AsyncMock()
        doc_id = uuid.uuid4()
        await svc.process_assets(doc_id, [{
            "type": "image", "page": 1, "binary": _png_bytes(), "mime": "image/png",
        }], "regulation")
        # OCR 文本 chunk 入库（文搜图，带页码前缀标记）
        svc.embed.batch_embed.assert_awaited_once()
        svc.milvus.insert.assert_awaited_once()
        svc.es.index_docs.assert_awaited_once()
        chunk_args = svc.chunk_repo.batch_create.await_args.args[0]
        assert "[图片] 页码P1" in chunk_args[0]["content"]
        assert chunk_args[0]["chunk_index"] == 1
        svc.chunk_repo.next_chunk_index.assert_awaited()
        # kb_doc_assets 元数据落表
        assets = svc.asset_repo.batch_create.await_args.args[0]
        assert len(assets) == 1
        assert assets[0].asset_type == "image"
        assert assets[0].page == 1
        assert assets[0].ocr_text == "识别出的制度文本"
        assert assets[0].storage_path.startswith(f"{doc_id}/")

    @pytest.mark.asyncio
    async def test_single_asset_failure_not_break_others(self, monkeypatch):
        """单张资产失败仅跳过（warning），其余资产正常落库。"""
        import uuid

        monkeypatch.setattr(settings, "KB_ASSETS_DIR", "/tmp")
        ocr = MagicMock()
        ocr.recognize = AsyncMock(side_effect=[RuntimeError("ocr down"), "第二张 OK"])
        svc = self._svc(ocr=ocr)
        svc.embed.batch_embed = AsyncMock(return_value=[[0.1] * 4])
        svc.milvus.insert = AsyncMock(return_value=["vid"])
        svc.es.index_docs = AsyncMock()
        doc_id = uuid.uuid4()
        await svc.process_assets(doc_id, [
            {"type": "image", "page": 1, "binary": _png_bytes(), "mime": "image/png"},
            {"type": "image", "page": 2, "binary": _png_bytes(), "mime": "image/png"},
        ], "regulation")
        # 第二张成功入库；第一张失败被捕获（process_assets 不抛错）
        assert svc.embed.batch_embed.await_count == 1
        chunk_args = svc.chunk_repo.batch_create.await_args.args[0]
        assert "第二张 OK" in chunk_args[0]["content"]
