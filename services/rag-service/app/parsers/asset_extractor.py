"""PDF/DOCX/PPTX 内嵌图片与表格抽取（V5，design D4/D5）。

- 图片：PDF 用 PyMuPDF 抽位图（矢量图经渲染接口转位图）、DOCX 用 inline_shapes
  并补扫 word/media（覆盖浮动/环绕图）、PPTX 用 picture shape；
  Pillow 等比缩放至 KB_ASSETS_MAX_SIZE；
- 表格：PDF 用 pdfplumber extract_tables、DOCX 用 doc.tables、PPTX 用 table shape；
  每行序列化 "表格(第{page}页): 列名1:值1,..."（保留行列结构不打散）。
"""
import asyncio
import io
import zipfile
from pathlib import Path

import structlog

from common.config.settings import settings

logger = structlog.get_logger()

_MIME_BY_FMT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "JPG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}


def image_extract_enabled() -> bool:
    """图片抽取开关（KB_IMAGE_EXTRACT_ENABLED）。"""
    return settings.KB_IMAGE_EXTRACT_ENABLED


def table_extract_enabled() -> bool:
    """表格抽取开关（KB_TABLE_EXTRACT_ENABLED）。"""
    return settings.KB_TABLE_EXTRACT_ENABLED


def assets_enabled() -> bool:
    """图片/表格抽取开关任一开启才执行资产抽取（开关全关时短路，行为与 V4 一致）。"""
    return image_extract_enabled() or table_extract_enabled()


def _scale_image(binary: bytes) -> tuple[bytes, str]:
    """Pillow 等比缩放至 KB_ASSETS_MAX_SIZE，返回 (新字节, mime)。"""
    from PIL import Image

    img = Image.open(io.BytesIO(binary))
    img.thumbnail((settings.KB_ASSETS_MAX_SIZE, settings.KB_ASSETS_MAX_SIZE))
    fmt = (img.format or "PNG").upper()
    mime = _MIME_BY_FMT.get(fmt, "image/png")
    save_fmt = fmt if fmt in ("JPEG", "GIF", "WEBP", "BMP") else "PNG"
    buf = io.BytesIO()
    img.save(buf, format=save_fmt)
    return buf.getvalue(), mime


class AssetExtractor:
    """按文件类型分发内嵌资产抽取；单资源失败跳过，不影响其余资产与正文解析。"""

    async def extract(self, file_path: Path) -> list[dict]:
        ext = file_path.suffix.lower()
        if ext == ".pdf":
            return await asyncio.to_thread(self._extract_pdf, file_path)
        if ext == ".docx":
            return await asyncio.to_thread(self._extract_docx, file_path)
        if ext == ".pptx":
            return await asyncio.to_thread(self._extract_pptx, file_path)
        return []

    @staticmethod
    def _resolve_table_location(headers: list[str], location: str | None) -> str:
        loc = (location or "").strip()
        if loc:
            return loc
        header_label = ", ".join(h for h in headers if h)
        return header_label or "未命名表"

    @staticmethod
    def _build_column_summary(
        headers: list[str], data_rows: list[list[str]], page: int, location: str
    ) -> str | None:
        if not headers or not data_rows:
            return None
        col_idx = 0
        for name in ("技术类别", "子系统"):
            if name in headers:
                col_idx = headers.index(name)
                break
        values: list[str] = []
        seen: set[str] = set()
        for row in data_rows:
            if col_idx < len(row) and row[col_idx]:
                val = str(row[col_idx]).strip()
                if val and val not in seen:
                    seen.add(val)
                    values.append(val)
        if not values:
            return None
        col_name = headers[col_idx] if col_idx < len(headers) else "列1"
        prefix = f"表格(第{page}页)[{location}]"
        summary = f"{prefix}: {col_name}: {', '.join(values)}"
        max_len = settings.KB_CHUNK_SIZE
        if len(summary) > max_len:
            summary = summary[: max_len - 3] + "..."
        return summary

    @staticmethod
    def _build_table_asset(
        page: int, rows: list[list[str]], location: str | None = None
    ) -> dict:
        """表格资产：首行为表头，后续行序列化（合并单元格值在行内保留，取左上角）。"""
        headers = [h for h in (rows[0] if rows else [])]
        data_rows = rows[1:] if rows else []
        loc = AssetExtractor._resolve_table_location(headers, location)
        prefix = f"表格(第{page}页)[{loc}]"
        serialized_lines: list[str] = []
        for row in data_rows:
            pairs = [
                f"{headers[j]}:{row[j]}"
                for j in range(min(len(headers), len(row)))
                if row[j]
            ]
            if pairs:
                serialized_lines.append(f"{prefix}: {', '.join(pairs)}")
        result: dict = {
            "type": "table",
            "page": page,
            "headers": headers,
            "rows": data_rows,
            "serialized_lines": serialized_lines,
            "location": loc,
        }
        summary_line = AssetExtractor._build_column_summary(headers, data_rows, page, loc)
        if summary_line:
            result["summary_line"] = summary_line
        return result

    def _extract_pdf(self, file_path: Path) -> list[dict]:
        import fitz  # PyMuPDF
        import pdfplumber

        assets: list[dict] = []
        extract_images = image_extract_enabled()
        extract_tables = table_extract_enabled()
        with fitz.open(str(file_path)) as doc:
            for page in doc:
                page_no = page.number + 1
                if extract_images:
                    # 1) 位图图片
                    for img in page.get_images(full=True):
                        try:
                            pix = fitz.Pixmap(doc, img[0])
                            if pix.n > 4:  # CMYK 等转 RGB
                                pix = fitz.Pixmap(fitz.csRGB, pix)
                            binary, mime = _scale_image(pix.tobytes("png"))
                            assets.append({"type": "image", "page": page_no, "binary": binary, "mime": mime})
                        except Exception:
                            continue  # 单张失败跳过
                    # 2) 矢量图经渲染接口转位图（仅渲染绘制区域，保守启发式防整页文本重复）
                    try:
                        drawings = page.get_drawings()
                        if drawings:
                            x0 = min(d["rect"].x0 for d in drawings)
                            y0 = min(d["rect"].y0 for d in drawings)
                            x1 = max(d["rect"].x1 for d in drawings)
                            y1 = max(d["rect"].y1 for d in drawings)
                            clip = fitz.Rect(x0, y0, x1, y1)
                            if clip.width < page.rect.width * 0.5 and clip.height < page.rect.height * 0.5:
                                zoom = max(1.0, settings.KB_ASSETS_MAX_SIZE / max(clip.width, clip.height))
                                pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(zoom, zoom), alpha=False)
                                binary, mime = _scale_image(pix.tobytes("png"))
                                assets.append({"type": "image", "page": page_no, "binary": binary, "mime": mime})
                    except Exception:
                        continue
        # 3) 内嵌表格
        if extract_tables:
            try:
                with pdfplumber.open(str(file_path)) as pdf:
                    for page in pdf.pages:
                        for table in page.extract_tables() or []:
                            rows = [[(c or "").strip() for c in row] for row in table]
                            if rows:
                                assets.append(self._build_table_asset(page.page_number, rows))
            except Exception:
                pass  # 表格抽取失败不影响图片与正文
        return assets

    def _extract_docx(self, file_path: Path) -> list[dict]:
        from docx import Document

        assets: list[dict] = []
        seen: set[bytes] = set()
        doc = Document(str(file_path))
        if image_extract_enabled():
            # 图片：inline_shapes 经关系部件取二进制
            for shape in doc.inline_shapes:
                try:
                    blip = shape._inline.graphic.graphicData.pic.blipFill.blip
                    part = doc.part.related_parts[blip.embed]
                    raw = part.blob
                    seen.add(raw[:64])
                    binary, mime = _scale_image(raw)
                    assets.append({"type": "image", "page": 0, "binary": binary, "mime": mime})
                except Exception:
                    continue
            # 浮动/环绕图等不在 inline_shapes 中，补扫 word/media（与上一步按 blob 去重）
            try:
                with zipfile.ZipFile(file_path) as zf:
                    names = sorted(
                        n for n in zf.namelist()
                        if n.startswith("word/media/") and not n.endswith("/")
                    )
                    for name in names:
                        try:
                            raw = zf.read(name)
                            key = raw[:64]
                            if not raw or key in seen:
                                continue
                            seen.add(key)
                            binary, mime = _scale_image(raw)
                            assets.append({"type": "image", "page": 0, "binary": binary, "mime": mime})
                        except Exception as e:
                            logger.debug(
                                "docx_media_image_skipped",
                                name=name,
                                error_type=type(e).__name__,
                            )
                            continue
            except (OSError, zipfile.BadZipFile):
                pass
        if table_extract_enabled():
            # 表格：doc.tables（DOCX 无页码概念，page=0）
            for table in doc.tables:
                rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
                if rows:
                    assets.append(self._build_table_asset(0, rows))
        return assets

    def _extract_pptx(self, file_path: Path) -> list[dict]:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        assets: list[dict] = []
        extract_images = image_extract_enabled()
        extract_tables = table_extract_enabled()
        prs = Presentation(str(file_path))
        for i, slide in enumerate(prs.slides, start=1):
            slide_title = ""
            try:
                if slide.shapes.title and slide.shapes.title.text:
                    slide_title = slide.shapes.title.text.strip()
            except Exception:
                pass
            for shape in slide.shapes:
                try:
                    if extract_images and shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                        binary, mime = _scale_image(shape.image.blob)
                        assets.append({"type": "image", "page": i, "binary": binary, "mime": mime})
                    elif extract_tables and shape.has_table:
                        rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                        if rows:
                            assets.append(
                                self._build_table_asset(i, rows, location=slide_title or None)
                            )
                except Exception:
                    continue
        return assets
