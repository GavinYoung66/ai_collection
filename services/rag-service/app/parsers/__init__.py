"""解析器工厂：按文件扩展名返回对应 Parser 实例。"""
from pathlib import Path

from app.parsers.base import DocumentParser
from app.parsers.docx_parser import DocxParser
from app.parsers.md_parser import MDParser
from app.parsers.pdf_parser import PDFParser
from app.parsers.text_parser import TextParser

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}


class ParserFactory:
    @staticmethod
    def get_parser(file_path: Path) -> DocumentParser:
        ext = file_path.suffix.lower()
        parsers: dict[str, type[DocumentParser]] = {
            ".txt": TextParser,
            ".md": MDParser,
            ".pdf": PDFParser,
            ".docx": DocxParser,
        }
        parser_cls = parsers.get(ext)
        if parser_cls is None:
            raise ValueError(f"Unsupported file format: {ext}")
        return parser_cls()

    @staticmethod
    def is_supported(filename: str) -> bool:
        return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


__all__ = [
    "DocumentParser",
    "TextParser",
    "MDParser",
    "PDFParser",
    "DocxParser",
    "ParserFactory",
    "SUPPORTED_EXTENSIONS",
]
