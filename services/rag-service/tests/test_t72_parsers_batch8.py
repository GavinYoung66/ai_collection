"""T72 / 批次 8：rag-service parsers 静默异常收口 单测。

验收判据（源码扫描守护）：
- asset_extractor.py 三处 continue（位图/矢量图/docx 图片）均带 debug 日志；
- pptx_parser.py 资产全量失败带 warning（全量丢弃值得暴露）；
- 对齐同文件 asset_table_extract_failed 已修形态（debug + exc_info + source）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"


class TestT72ParsersSilentExceptionClosure:
    def test_asset_extractor_three_continues_logged(self):
        """位图/矢量图/docx 三处 continue：日志事件逐一存在。"""
        src = (_APP / "parsers/asset_extractor.py").read_text(encoding="utf-8")
        for event in (
            "asset_bitmap_extract_failed",
            "asset_vector_render_failed",
            "asset_docx_image_extract_failed",
        ):
            assert event in src, f"asset_extractor.py 缺收口日志事件: {event}"

    def test_continues_carry_exc_info_and_source(self):
        """三处 debug 均带 exc_info=True 与 source 上下文（对齐表格抽取已修形态）。"""
        src = (_APP / "parsers/asset_extractor.py").read_text(encoding="utf-8")
        assert src.count("asset_bitmap_extract_failed") == 1
        bitmap_idx = src.find("asset_bitmap_extract_failed")
        assert "exc_info=True" in src[bitmap_idx : bitmap_idx + 200]
        assert "source=str(file_path)" in src[bitmap_idx : bitmap_idx + 200]
        vector_idx = src.find("asset_vector_render_failed")
        assert "exc_info=True" in src[vector_idx : vector_idx + 200]

    def test_pptx_all_dropped_warning(self):
        """pptx 资产全量失败：warning（降级语义值得暴露）。"""
        src = (_APP / "parsers/pptx_parser.py").read_text(encoding="utf-8")
        assert "pptx_assets_extract_failed_all_dropped" in src
        idx = src.find("pptx_assets_extract_failed_all_dropped")
        assert "warning" in src[max(0, idx - 80) : idx]

    def test_table_extract_reference_form_still_present(self):
        """参照形态（表格抽取 debug+exc_info）未被本批改动破坏（回归）。"""
        src = (_APP / "parsers/asset_extractor.py").read_text(encoding="utf-8")
        assert "asset_table_extract_failed" in src
        assert "exc_info=True" in src