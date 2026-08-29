"""解析器工厂：按文件扩展名返回对应 Parser 实例。"""
from pathlib import Path

from app.parsers.audio_parser import AudioParser
from app.parsers.base import DocumentParser
from app.parsers.docx_parser import DocxParser
from app.parsers.image_parser import ImageParser
from app.parsers.md_parser import MDParser
from app.parsers.pdf_parser import PDFParser
from app.parsers.pptx_parser import PptxParser
from app.parsers.text_parser import TextParser
from app.parsers.xlsx_parser import XlsxParser

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg",
    ".mp3", ".wav", ".m4a", ".xlsx", ".pptx", ".amr", ".wma",
}


class ParserFactory:
    @staticmethod
    def get_parser(file_path: Path) -> DocumentParser:
        ext = file_path.suffix.lower()
        parsers: dict[str, type[DocumentParser]] = {
            ".txt": TextParser,
            ".md": MDParser,
            ".pdf": PDFParser,
            ".docx": DocxParser,
            ".png": ImageParser,
            ".jpg": ImageParser,
            ".jpeg": ImageParser,
            ".mp3": AudioParser,
            ".wav": AudioParser,
            ".m4a": AudioParser,
            ".xlsx": XlsxParser,
            ".pptx": PptxParser,
            ".amr": AudioParser,
            ".wma": AudioParser,
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
    "ImageParser",
    "AudioParser",
    "XlsxParser",
    "PptxParser",
    "ParserFactory",
    "SUPPORTED_EXTENSIONS",
]
