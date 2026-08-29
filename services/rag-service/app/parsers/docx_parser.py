"""DOCX 解析器：python-docx 提取段落文本。"""
import asyncio
from pathlib import Path

from app.parsers.base import DocumentParser


class DocxParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        def _extract() -> str:
            from docx import Document

            doc = Document(str(file_path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs)

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
