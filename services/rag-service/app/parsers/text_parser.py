"""TXT 解析器：UTF-8 读取 + 编码检测 fallback。"""
import asyncio
from pathlib import Path

from app.parsers.base import DocumentParser


class TextParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        def _read() -> str:
            try:
                return file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw = file_path.read_bytes()
                for enc in ("gbk", "gb2312", "big5", "latin-1"):
                    try:
                        return raw.decode(enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return raw.decode("utf-8", errors="replace")

        return await asyncio.to_thread(_read)
