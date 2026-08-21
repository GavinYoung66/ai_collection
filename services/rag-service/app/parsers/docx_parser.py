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
