"""文档管理与解析测试。"""
import asyncio
from pathlib import Path

import pytest

from app.parsers import ParserFactory
from app.parsers.text_parser import TextParser
from app.parsers.md_parser import MDParser
from app.services.chunk_service import ChunkService


class TestParserFactory:
    def test_is_supported_txt(self):
        assert ParserFactory.is_supported("test.txt")

    def test_is_supported_md(self):
        assert ParserFactory.is_supported("test.md")

    def test_is_supported_pdf(self):
        assert ParserFactory.is_supported("test.pdf")

    def test_is_supported_docx(self):
        assert ParserFactory.is_supported("test.docx")

    def test_not_supported_exe(self):
        assert not ParserFactory.is_supported("test.exe")

    def test_get_parser_txt(self):
        parser = ParserFactory.get_parser(Path("test.txt"))
        assert isinstance(parser, TextParser)

    def test_get_parser_md(self):
        parser = ParserFactory.get_parser(Path("test.md"))
        assert isinstance(parser, MDParser)

    def test_get_parser_unsupported(self):
        with pytest.raises(ValueError, match="Unsupported"):
            ParserFactory.get_parser(Path("test.exe"))


class TestTextParser:
    def test_extract_text(self, sample_txt_file):
        parser = TextParser()
        text = asyncio.run(parser.extract_text(sample_txt_file))
        assert "催收合规制度" in text
        assert "第一条" in text

    def test_extract_text_utf8(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text("中文测试内容", encoding="utf-8")
        parser = TextParser()
        text = asyncio.run(parser.extract_text(file_path))
        assert "中文测试内容" in text


class TestMDParser:
    def test_extract_text_removes_markdown(self, sample_md_file):
        parser = MDParser()
        text = asyncio.run(parser.extract_text(sample_md_file))
        assert "催收合规制度" in text
        assert "第一条" in text
        assert "不得" in text
        assert "#" not in text or text.count("#") == 0


class TestChunkServiceClean:
    def test_clean_removes_empty_lines(self, chunk_service):
        text = "第一行\n\n\n第二行\n\n\n\n第三行"
        result = chunk_service.clean(text)
        assert "第一行" in result
        assert "第二行" in result
        assert "第三行" in result
        assert "\n\n\n" not in result

    def test_clean_removes_extra_spaces(self, chunk_service):
        text = "第一行   有多余空格"
        result = chunk_service.clean(text)
        assert "  " not in result or result.count("  ") == 0

    def test_clean_strips_whitespace(self, chunk_service):
        text = "  内容  \n  内容2  "
        result = chunk_service.clean(text)
        lines = result.split("\n")
        assert lines[0].strip() == lines[0]


class TestChunkServiceSplit:
    def test_split_by_paragraph(self, chunk_service, sample_text):
        chunks = chunk_service.split(sample_text, "regulation")
        assert len(chunks) > 0
        for chunk in chunks:
            assert len(chunk["content"]) >= 10
            assert chunk["category"] == "regulation"

    def test_split_filters_short_chunks(self, chunk_service):
        text = "短\n\n这是一个足够长的段落，内容超过最短长度限制。"
        chunks = chunk_service.split(text, "regulation")
        short_chunks = [c for c in chunks if len(c["content"]) < 10]
        assert len(short_chunks) == 0

    def test_split_assigns_chunk_index(self, chunk_service, sample_text):
        chunks = chunk_service.split(sample_text, "regulation")
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_split_long_paragraph(self, chunk_service):
        long_text = "这是一个很长的段落。" * 50
        chunks = chunk_service.split(long_text, "regulation")
        assert len(chunks) > 1

    def test_split_empty_text(self, chunk_service):
        chunks = chunk_service.split("", "regulation")
        assert len(chunks) == 0

    def test_split_only_short_chunks(self, chunk_service):
        text = "短\n\n短\n\n短"
        chunks = chunk_service.split(text, "regulation")
        assert len(chunks) == 0
