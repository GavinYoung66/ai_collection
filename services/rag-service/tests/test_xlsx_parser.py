"""Excel 解析器单元测试（V5，design D2；tasks 11.1）。

覆盖：多 sheet 全入库 / sheet 名前缀冗余 / 行切块序列化 / 空行全空值跳过 /
空单元格不序列化 / 损坏 xlsx → ValueError（ingest 转 A3001）/ 表头行之前标题跳过 /
KB_EXCEL_SHEET_ALL=false 仅首 sheet。
"""
from pathlib import Path

import openpyxl
import pytest

from app.parsers.xlsx_parser import XlsxParser
from app.settings import settings


def _make_xlsx(path: Path, sheets: dict[str, list[list]]) -> None:
    """用 openpyxl 生成真实 xlsx：{sheet名: rows}。"""
    wb = openpyxl.Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = name
        for row in rows:
            ws.append(row)
    wb.save(str(path))


class TestXlsxParser:
    @pytest.mark.asyncio
    async def test_multi_sheet_all_ingested(self, tmp_path, monkeypatch):
        """多 sheet 全部入库（KB_EXCEL_SHEET_ALL=true 默认）。"""
        monkeypatch.setattr(settings, "KB_EXCEL_SHEET_ALL", True)
        f = tmp_path / "multi.xlsx"
        _make_xlsx(f, {
            "材料表": [["材料", "要求"], ["身份证", "原件"]],
            "流程表": [["步骤", "说明"], ["1", "登记"]],
        })
        text = await XlsxParser().extract_text(f)
        assert "材料表" in text and "流程表" in text
        assert "材料:身份证, 要求:原件" in text
        assert "步骤:1, 说明:登记" in text

    @pytest.mark.asyncio
    async def test_sheet_name_prefix_in_each_line(self, tmp_path, monkeypatch):
        """sheet 名作为定位前缀（表格(第0页)[sheet名]: ...）。"""
        monkeypatch.setattr(settings, "KB_EXCEL_SHEET_ALL", True)
        f = tmp_path / "prefix.xlsx"
        _make_xlsx(f, {"材料表": [["材料", "要求"], ["身份证", "原件"], ["合同", "复印件"]]})
        text = await XlsxParser().extract_text(f)
        lines = [ln for ln in text.split("\n") if ln.strip()]
        assert lines[0].startswith("表格(第0页)[材料表]: "), lines[0]
        assert lines[1].startswith("表格(第0页)[材料表]: "), lines[1]

    @pytest.mark.asyncio
    async def test_row_serialization_format(self, tmp_path, monkeypatch):
        """行切块序列化格式：列名:值 逗号连接，空单元格不序列化。"""
        monkeypatch.setattr(settings, "KB_EXCEL_SHEET_ALL", False)
        f = tmp_path / "fmt.xlsx"
        _make_xlsx(f, {"表": [["a", "b", "c"], [None, "v2", ""]]})
        text = await XlsxParser().extract_text(f)
        assert "b:v2" in text
        assert "a:" not in text and "c:" not in text

    @pytest.mark.asyncio
    async def test_empty_row_skipped(self, tmp_path, monkeypatch):
        """空行与全空值行跳过（不产生 chunk）。"""
        monkeypatch.setattr(settings, "KB_EXCEL_SHEET_ALL", False)
        f = tmp_path / "empty.xlsx"
        _make_xlsx(f, {"表": [["a"], [None], [""], ["v"]]})
        text = await XlsxParser().extract_text(f)
        assert text.count("\n") == 0  # 仅数据行 "表: a:v"
        assert "a:v" in text

    @pytest.mark.asyncio
    async def test_header_row_before_skip(self, tmp_path, monkeypatch):
        """表头行（KB_EXCEL_HEADER_ROW=2）之前视为标题跳过。"""
        monkeypatch.setattr(settings, "KB_EXCEL_HEADER_ROW", 2)
        monkeypatch.setattr(settings, "KB_EXCEL_SHEET_ALL", False)
        f = tmp_path / "header2.xlsx"
        _make_xlsx(f, {"表": [["标题行"], ["a", "b"], ["1", "2"]]})
        text = await XlsxParser().extract_text(f)
        assert "标题行" not in text
        assert "a:1, b:2" in text

    @pytest.mark.asyncio
    async def test_sheet_all_false_only_first(self, tmp_path, monkeypatch):
        """KB_EXCEL_SHEET_ALL=false → 仅首个 sheet 入库。"""
        monkeypatch.setattr(settings, "KB_EXCEL_SHEET_ALL", False)
        f = tmp_path / "first.xlsx"
        _make_xlsx(f, {"A表": [["x"], ["1"]], "B表": [["y"], ["2"]]})
        text = await XlsxParser().extract_text(f)
        assert "A表" in text and "B表" not in text

    def test_corrupt_xlsx_raises_value_error(self, tmp_path):
        """损坏/加密 xlsx → ValueError（ingest 沿既有路径转 A3001）。"""
        f = tmp_path / "corrupt.xlsx"
        f.write_bytes(b"not a real xlsx file")
        with pytest.raises(ValueError, match="损坏|加密"):
            XlsxParser()._extract(f)
