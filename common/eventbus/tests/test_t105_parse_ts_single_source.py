"""T105：eventbus `_parse_ts` 收敛 time_parse 单源（spec runtime-correctness：单源收敛完整性）。

修复前：本地实现 naive 按 UTC——`replay_dlq` 运维传不带时区的窗口（since/until）
偏移 8 小时。修复后：naive 按业务时区 Asia/Shanghai 解释（平台约定 spec §6.2）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common.eventbus.rocketmq import _parse_ts


class TestT105ParseTsSingleSource:
    def test_none_passthrough(self):
        assert _parse_ts(None) is None

    def test_naive_datetime_bound_to_business_tz(self):
        """naive datetime 按 Asia/Shanghai 解释（+08:00），不再按 UTC。"""
        out = _parse_ts(datetime(2026, 1, 1, 8, 0, 0))
        assert out is not None
        assert out.utcoffset() == timedelta(hours=8)
        # 上海 08:00 == UTC 00:00（旧实现会得出 08:00 UTC，偏移 8 小时）
        assert out.astimezone(timezone.utc).hour == 0

    def test_naive_string_bound_to_business_tz(self):
        out = _parse_ts("2026-01-01 08:00:00")
        assert out is not None
        assert out.utcoffset() == timedelta(hours=8)
        assert out.astimezone(timezone.utc).hour == 0

    def test_aware_datetime_passthrough(self):
        aware = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert _parse_ts(aware) == aware

    def test_z_suffix_parsed_as_utc(self):
        out = _parse_ts("2026-01-01T00:00:00Z")
        assert out is not None
        assert out.utcoffset() == timedelta(0)

    def test_replay_dlq_naive_window_no_8h_drift(self):
        """spec 场景：运维传 naive 窗口按 Asia/Shanghai 解释，不偏移 8 小时。"""
        local_8am = _parse_ts("2026-01-01 08:00:00")
        assert local_8am is not None
        # 旧实现：08:00 naive → 08:00 UTC（=上海 16:00），运维窗口整体右移 8h
        assert local_8am.astimezone(timezone.utc).hour == 0

    def test_invalid_string_raises_value_error(self):
        import pytest

        with pytest.raises(ValueError):
            _parse_ts("not-a-time")