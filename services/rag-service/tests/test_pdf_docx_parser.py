"""PDF / DOCX 二进制文档解析器测试（DD-03 §9.2 断言2/3）。

构造内存级模拟文件（python-docx 生成 DOCX、fitz 生成 PDF），
走真实 pdfplumber / python-docx 解析链路，校验解析输出与页眉页脚过滤逻辑。
"""
import asyncio
from pathlib import Path

from app.parsers.docx_parser import DocxParser
from app.parsers.pdf_parser import PDFParser


def _make_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))


def _make_pdf(path: Path, pages: list[list[str]]) -> None:
    import fitz

    pdf = fitz.open()
    for lines in pages:
        page = pdf.new_page()
        y = 50
        for line in lines:
            page.insert_text((50, y), line)
            y += 20
    pdf.save(str(path))
    pdf.close()


class TestDocxParser:
    def test_extract_text_joins_paragraphs(self, tmp_path):
        file_path = tmp_path / "sample.docx"
        _make_docx(
            file_path,
            [
                "Collection Compliance Policy",
                "First rule: agents must not use threats.",
                "Second rule: overdue 90 days may apply for negotiation.",
            ],
        )
        parser = DocxParser()
        text = asyncio.run(parser.extract_text(file_path))
        assert "Collection Compliance Policy" in text
        assert "First rule" in text
        assert "Second rule" in text

    def test_extract_text_skips_empty_paragraphs(self, tmp_path):
        file_path = tmp_path / "sample.docx"
        _make_docx(file_path, ["First rule text.", "", "", "Second rule text."])
        parser = DocxParser()
        text = asyncio.run(parser.extract_text(file_path))
        assert "First rule text." in text
        assert "Second rule text." in text
        assert "\n\n\n" not in text


class TestPDFParser:
    def test_extract_text_multiple_pages(self, tmp_path):
        file_path = tmp_path / "sample.pdf"
        _make_pdf(file_path, [["Page one content"], ["Page two content"]])
        parser = PDFParser()
        text = asyncio.run(parser.extract_text(file_path))
        assert "Page one content" in text
        assert "Page two content" in text

    def test_extract_text_filters_header_footer(self, tmp_path):
        file_path = tmp_path / "sample.pdf"
        lines = [
            "Header1",
            "Header2",
            "Body line one",
            "Body line two",
            "Body line three",
            "Footer1",
            "Footer2",
        ]
        _make_pdf(file_path, [lines])
        parser = PDFParser()
        text = asyncio.run(parser.extract_text(file_path))
        assert "Body line one" in text
        assert "Body line three" in text
        assert "Header1" not in text
        assert "Footer2" not in text
