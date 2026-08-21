"""Markdown 解析器：去除标记符号，保留纯文本。"""
import asyncio
import re
from pathlib import Path

from app.parsers.base import DocumentParser

_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_LIST_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s+", re.MULTILINE)
_HR_RE = re.compile(r"^---+$", re.MULTILINE)


class MDParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        def _parse() -> str:
            text = file_path.read_text(encoding="utf-8")
            text = _IMAGE_RE.sub(r"\1", text)
            text = _LINK_RE.sub(r"\1", text)
            text = _HEADING_RE.sub("", text)
            text = _BOLD_RE.sub(r"\1", text)
            text = _ITALIC_RE.sub(r"\1", text)
            text = _CODE_RE.sub(r"\1", text)
            text = _LIST_RE.sub("", text)
            text = _BLOCKQUOTE_RE.sub("", text)
            text = _HR_RE.sub("", text)
            return text

        return await asyncio.to_thread(_parse)
