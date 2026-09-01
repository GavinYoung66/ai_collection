"""内嵌资产抽取单元测试（V5，design D4/D5；tasks 11.3）。

覆盖：PDF/DOCX/PPTX 内嵌图片抽取（含矢量图渲染位图路径）/ 图片 OCR 文本 chunk 入库
（mock ocr-service）/ kb_doc_assets 元数据落表 / 单张失败不影响正文 / 开关关闭零资产。
"""
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.parsers.asset_extractor import (
    AssetExtractor,
    assets_enabled,
    image_extract_enabled,
    table_extract_enabled,
)
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
    async def test_pdf_bitmap_image_extracted(self, tmp_path, monkeypatch):
        """PDF 位图图片抽取（get_images → Pixmap → 缩放 PNG）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
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
    async def test_pdf_vector_render_path(self, tmp_path, monkeypatch):
        """矢量图经渲染接口转位图路径（get_drawings → clip → get_pixmap）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
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

    def test_docx_inline_shape_extracted(self, tmp_path, monkeypatch):
        """DOCX inline_shapes 图片抽取（related_parts 二进制）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
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

    def test_docx_media_fallback_extracts_when_inline_empty(self, tmp_path, monkeypatch):
        """inline_shapes 为空时，仍从 word/media 抽出浮动图（png）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        import zipfile

        f = tmp_path / "float.docx"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
            )
            zf.writestr("word/media/image1.png", _png_bytes())
        with patch("docx.Document") as mo:
            doc = mo.return_value
            doc.inline_shapes = []
            doc.tables = []
            assets = AssetExtractor()._extract_docx(f)
        assert len(assets) == 1 and assets[0]["type"] == "image" and assets[0]["page"] == 0
        assert assets[0]["binary"]

    def test_docx_media_dedupes_inline_blob(self, tmp_path, monkeypatch):
        """同一张图既在 inline_shapes 又在 word/media 时只入一份。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        import zipfile

        png = _png_bytes()
        f = tmp_path / "dup.docx"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("word/media/image1.png", png)
        with patch("docx.Document") as mo:
            doc = mo.return_value
            shape = MagicMock()
            blip = shape._inline.graphic.graphicData.pic.blipFill.blip
            blip.embed = "rId1"
            part = MagicMock()
            part.blob = png
            doc.part.related_parts.__getitem__.return_value = part
            doc.inline_shapes = [shape]
            doc.tables = []
            assets = AssetExtractor()._extract_docx(f)
        assert len(assets) == 1 and assets[0]["type"] == "image"

    def test_pptx_picture_extracted(self, tmp_path, monkeypatch):
        """PPTX picture shape 图片抽取（page=i）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
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

    def test_docx_image_only_no_table_assets(self, tmp_path, monkeypatch):
        """只开图不开表 → 不产生 table 资产。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        f = tmp_path / "img_only.docx"
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
            table = MagicMock()
            table.rows = [MagicMock(cells=[MagicMock(text="a")])]
            doc.tables = [table]
            assets = AssetExtractor()._extract_docx(f)
        assert all(a["type"] == "image" for a in assets)
        assert not any(a["type"] == "table" for a in assets)

    def test_docx_table_only_no_image_assets(self, tmp_path, monkeypatch):
        """只开表不开图 → 不产生 image 资产。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", True)
        f = tmp_path / "table_only.docx"
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
            table = MagicMock()
            row = MagicMock()
            row.cells = [MagicMock(text="x")]
            table.rows = [row]
            doc.tables = [table]
            assets = AssetExtractor()._extract_docx(f)
        assert all(a["type"] == "table" for a in assets)
        assert not any(a["type"] == "image" for a in assets)

    def test_switch_helpers(self, monkeypatch):
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        assert image_extract_enabled() is True
        assert table_extract_enabled() is False


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
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
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
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        ocr = MagicMock()
        ocr.recognize = AsyncMock(side_effect=[RuntimeError("ocr down"), "第二张 OK"])
        svc = self._svc(ocr=ocr)
        svc.embed.batch_embed = AsyncMock(return_value=[[0.1] * 4])
        svc.milvus.insert = AsyncMock(return_value=["vid"])
        svc.es.index_docs = AsyncMock()
        # 共享 Session：rollback 必须是 AsyncMock，便于断言「不得调用」
        svc.chunk_repo.db = MagicMock()
        svc.chunk_repo.db.rollback = AsyncMock()
        doc_id = uuid.uuid4()
        await svc.process_assets(doc_id, [
            {"type": "image", "page": 1, "binary": _png_bytes(), "mime": "image/png"},
            {"type": "image", "page": 2, "binary": _png_bytes(), "mime": "image/png"},
        ], "regulation")
        # 第二张成功入库；第一张失败被捕获（process_assets 不抛错）
        assert svc.embed.batch_embed.await_count == 1
        batch_args = svc.embed.batch_embed.await_args.args[0]
        assert len(batch_args) == 1
        assert "第二张 OK" in batch_args[0]
        # 决策 6 / 任务 7.1：单资产失败不得全量 rollback 共享 Session
        svc.chunk_repo.db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ocr_failure_does_not_block_table_batch(self, monkeypatch):
        """OCR 失败 skip 后，同文档表格仍走批量写入（不得因 rollback 导致后续失败）。"""
        import uuid

        monkeypatch.setattr(settings, "KB_ASSETS_DIR", "/tmp")
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", True)
        ocr = MagicMock()
        ocr.recognize = AsyncMock(side_effect=RuntimeError("ocr unreachable"))
        svc = self._svc(ocr=ocr)
        svc.embed.batch_embed = AsyncMock(return_value=[[0.1] * 4, [0.2] * 4])
        svc.milvus.insert = AsyncMock(return_value=["vid1", "vid2"])
        svc.es.index_docs = AsyncMock()
        svc.chunk_repo.db = MagicMock()
        svc.chunk_repo.db.rollback = AsyncMock()
        doc_id = uuid.uuid4()
        await svc.process_assets(doc_id, [
            {"type": "image", "page": 0, "binary": _png_bytes(), "mime": "image/png"},
            {
                "type": "table", "page": 0,
                "headers": ["期数", "金额"],
                "rows": [["1", "100"], ["2", "200"]],
                "serialized_lines": [
                    "表格(第0页): 期数:1, 金额:100",
                    "表格(第0页): 期数:2, 金额:200",
                ],
            },
        ], "regulation")
        svc.chunk_repo.db.rollback.assert_not_awaited()
        svc.embed.batch_embed.assert_awaited_once()
        assert len(svc.embed.batch_embed.await_args.args[0]) == 2
        chunk_args = svc.chunk_repo.batch_create.await_args.args[0]
        assert len(chunk_args) == 2
        assert all("表格" in c["content"] for c in chunk_args)
        assets = svc.asset_repo.batch_create.await_args.args[0]
        assert len(assets) == 1
        assert assets[0].asset_type == "table"

    @pytest.mark.asyncio
    async def test_multi_image_single_batch_embed(self, monkeypatch):
        """多张图片 OCR 文本一次 batch_embed。"""
        import uuid

        monkeypatch.setattr(settings, "KB_ASSETS_DIR", "/tmp")
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        ocr = MagicMock()
        ocr.recognize = AsyncMock(side_effect=["图一", "图二"])
        svc = self._svc(ocr=ocr)
        svc.embed.batch_embed = AsyncMock(return_value=[[0.1] * 4, [0.2] * 4])
        svc.milvus.insert = AsyncMock(return_value=["vid1", "vid2"])
        svc.es.index_docs = AsyncMock()
        doc_id = uuid.uuid4()
        await svc.process_assets(doc_id, [
            {"type": "image", "page": 1, "binary": _png_bytes(), "mime": "image/png"},
            {"type": "image", "page": 2, "binary": _png_bytes(), "mime": "image/png"},
        ], "regulation")
        svc.embed.batch_embed.assert_awaited_once()
        assert len(svc.embed.batch_embed.await_args.args[0]) == 2
        chunk_args = svc.chunk_repo.batch_create.await_args.args[0]
        assert len(chunk_args) == 2

    @pytest.mark.asyncio
    async def test_process_assets_filters_table_when_image_only(self, monkeypatch):
        """落库层：只开图时丢弃 table 类型。"""
        import uuid

        monkeypatch.setattr(settings, "KB_ASSETS_DIR", "/tmp")
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        svc = self._svc()
        svc.embed.batch_embed = AsyncMock(return_value=[[0.1] * 4])
        svc.milvus.insert = AsyncMock(return_value=["vid"])
        svc.es.index_docs = AsyncMock()
        doc_id = uuid.uuid4()
        await svc.process_assets(doc_id, [{
            "type": "table", "page": 1,
            "headers": ["a"], "rows": [["v"]],
            "serialized_lines": ["表格(第1页): a:v"],
        }], "regulation")
        svc.embed.batch_embed.assert_not_awaited()
        svc.asset_repo.batch_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_index_failure_propagates(self, monkeypatch):
        """批量 embedding 失败向上抛出（由 ingest 回滚）。"""
        import uuid

        monkeypatch.setattr(settings, "KB_ASSETS_DIR", "/tmp")
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        ocr = MagicMock()
        ocr.recognize = AsyncMock(return_value="text")
        svc = self._svc(ocr=ocr)
        svc.embed.batch_embed = AsyncMock(side_effect=RuntimeError("embed down"))
        doc_id = uuid.uuid4()
        with pytest.raises(RuntimeError, match="embed down"):
            await svc.process_assets(doc_id, [{
                "type": "image", "page": 1, "binary": _png_bytes(), "mime": "image/png",
            }], "regulation")
        # 任务 7.3：批量失败仍向上抛出，由 ingest 走 A3003 回滚契约（不在此处吞掉）
