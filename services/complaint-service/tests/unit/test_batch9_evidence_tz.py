"""批次 9：complaint-service 判责证据时间线时区单源（T82 / 9.5）单测。

验收判据：naive 与 aware 混合输入排序符合预期（无 8 小时错位）——
naive 统一绑业务时区 Asia/Shanghai（time_parse 单源）。
"""
from __future__ import annotations

from app.service.ordered_evidence import _ts_key


class TestTsKeyBusinessTimezone:
    def test_naive_equals_shanghai_aware(self):
        """上海 10:00 naive 与 10:00+08:00 aware 同时刻（无 8 小时错位）。"""
        naive = "2026-11-05T10:00:00"
        aware = "2026-11-05T10:00:00+08:00"
        assert _ts_key(naive) == _ts_key(aware)

    def test_mixed_sort_no_8h_shift(self):
        """混合排序：5 秒差保持 5 秒差（进程本地时区为 UTC 时曾错位 8 小时）。"""
        items = [
            ("aware_first", "2026-11-05T10:00:00+08:00"),
            ("naive_second", "2026-11-05T10:00:05"),
        ]
        ordered = sorted(items, key=lambda x: _ts_key(x[1]))
        assert [x[0] for x in ordered] == ["aware_first", "naive_second"]

    def test_datetime_input_same_semantics(self):
        """datetime 输入与 ISO 串输入同语义（统一走 time_parse）。"""
        from datetime import datetime

        assert _ts_key(datetime(2026, 11, 5, 10, 0, 0)) == _ts_key(
            "2026-11-05T10:00:00"
        )

    def test_none_and_invalid_sorted_last(self):
        """None / 非法值排最后（不影响可解析条目的相对顺序，回归不变）。"""
        assert _ts_key(None) == float("inf")
        assert _ts_key("garbage") == float("inf")
        valid = _ts_key("2026-11-05T10:00:00")
        assert valid < float("inf")