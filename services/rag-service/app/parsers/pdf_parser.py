"""PDF 解析器：pdfplumber 逐页提取文本；章节页保留完整行，减少流程图信息丢失。"""
import asyncio
import re
from pathlib import Path

from app.parsers.base import DocumentParser
from app.services.chunk_service import normalize_text

_SECTION_LINE_RE = re.compile(r"\d+\.\d+")
_NOISE_LINES = frozenset({"视图", ""})


def _trim_page_lines(lines: list[str]) -> list[str]:
    """页眉页脚裁剪：含小节号（5.2）的章节页不裁切，避免流程图标题丢失。"""
    lines = [ln for ln in lines if ln.strip() not in _NOISE_LINES]
    if len(lines) <= 4:
        return lines
    if any(_SECTION_LINE_RE.search(ln) for ln in lines):
        return lines
    return lines[2:-2]


class PDFParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        def _extract() -> str:
            import pdfplumber

            pages_text: list[str] = []
            with pdfplumber.open(str(file_path)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    lines = page_text.split("\n")
                    trimmed = _trim_page_lines(lines)
                    pages_text.append("\n".join(trimmed))
            return normalize_text("\n\n".join(pages_text))

        return await asyncio.to_thread(_extract)

    async def extract_with_assets(self, file_path: Path) -> tuple[str, list]:
        """V5：正文文本 + 内嵌图片/表格资产（design D4/D5）；资产失败仅丢弃不影响正文。"""
        from app.parsers.asset_extractor import AssetExtractor, assets_enabled

        text = await self.extract_text(file_path)
        if not assets_enabled():
            return text, []
        try:
            assets = await AssetExtractor().extract(file_path)
        except Exception:
            assets = []
        return text, assets
