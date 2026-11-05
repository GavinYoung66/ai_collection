"""T51 / 3.1：time_parse 时间解析单一事实源 单测。

验收判据：
- naive datetime 绑定业务时区（Asia/Shanghai）；aware datetime 原样返回；
- ISO 字符串容忍 ``Z`` 后缀；非法串抛 ValueError；
- float epoch 防御分支：按 UTC epoch 秒收敛（残留 float 旧数据不崩不误删）；
- None 原样返回 None（调用方保守跳过）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from common.utils.time_parse import (
    BUSINESS_TZ,
    parse_epoch_or_datetime,
    parse_naive_or_aware,
)


class TestParseNaiveOrAware:
    def test_naive_datetime_bound_to_shanghai(self):
        naive = datetime(2026, 11, 5, 12, 0, 0)
        result = parse_naive_or_aware(naive)
        assert result.tzinfo is not None
        assert result.utcoffset() == BUSINESS_TZ.utcoffset(result)

    def test_naive_datetime_custom_default_tz(self):
        naive = datetime(2026, 11, 5, 12, 0, 0)
        utc = ZoneInfo("UTC")
        result = parse_naive_or_aware(naive, default_tz=utc)
        assert result.utcoffset().total_seconds() == 0

    def test_aware_datetime_returned_as_is(self):
        aware = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        assert parse_naive_or_aware(aware) is aware

    def test_iso_string_with_z_suffix(self):
        result = parse_naive_or_aware("2026-11-05T12:00:00Z")
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0

    def test_iso_string_naive_bound_to_shanghai(self):
        result = parse_naive_or_aware("2026-11-05T12:00:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == BUSINESS_TZ.utcoffset(result)

    def test_invalid_string_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_naive_or_aware("not-a-time")

    def test_unsupported_type_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_naive_or_aware(12345)  # type: ignore[arg-type]


class TestParseEpochOrDatetime:
    def test_float_epoch_converged_to_utc(self):
        epoch = 1794244800.0  # 2026-11-09T12:00:00Z 附近的固定锚点
        result = parse_epoch_or_datetime(epoch)
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0
        assert result == datetime.fromtimestamp(epoch, tz=timezone.utc)

    def test_int_epoch_same_as_float(self):
        assert parse_epoch_or_datetime(1794244800) == parse_epoch_or_datetime(
            1794244800.0
        )

    def test_datetime_delegates(self):
        aware = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        assert parse_epoch_or_datetime(aware) is aware

    def test_string_delegates_with_z(self):
        result = parse_epoch_or_datetime("2026-11-05T12:00:00Z")
        assert result is not None
        assert result.utcoffset().total_seconds() == 0

    def test_none_returns_none(self):
        assert parse_epoch_or_datetime(None) is None

    def test_bool_rejected(self):
        with pytest.raises(ValueError):
            parse_epoch_or_datetime(True)

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            parse_epoch_or_datetime("garbage")

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError):
            parse_epoch_or_datetime({"ts": 1})