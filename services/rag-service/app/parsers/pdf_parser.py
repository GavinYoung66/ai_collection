"""PDF 解析器：pdfplumber 逐页提取文本，过滤页眉页脚。"""
import asyncio
from pathlib import Path

from app.parsers.base import DocumentParser


class PDFParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        def _extract() -> str:
            import pdfplumber

            pages_text: list[str] = []
            with pdfplumber.open(str(file_path)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    lines = page_text.split("\n")
                    if len(lines) > 4:
                        lines = lines[2:-2]
                    pages_text.append("\n".join(lines))
            return "\n\n".join(pages_text)

        return await asyncio.to_thread(_extract)
