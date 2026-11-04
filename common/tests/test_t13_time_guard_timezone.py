"""T13：外呼时段判定的时区确定性单测。

核心验证：容器 ``TZ=UTC`` 下，业务时区（call-service ``CALL_TIMEZONE=Asia/Shanghai``）
的时段判定与本地北京时间一致；合规模块由调用方注入参数（T33 后 common 不再读全局业务配置），
固定时钟注入，不依赖真实时间。
"""
import time as time_module
from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.utils.time_guard import check_call_time

BEIJING = "Asia/Shanghai"
ALLOWED_HOURS = "08:00-21:00"


@pytest.fixture
def utc_container(monkeypatch):
    """模拟容器 TZ=UTC：进程本地时区与业务时区不一致。"""
    monkeypatch.setenv("TZ", "UTC")
    time_module.tzset()
    try:
        yield
    finally:
        monkeypatch.undo()
        time_module.tzset()


def _check(now: datetime, timezone_name: str = BEIJING) -> None:
    check_call_time(now, timezone_name=timezone_name, allowed_hours=ALLOWED_HOURS)


def _assert_allowed(now: datetime, timezone_name: str = BEIJING) -> None:
    assert _check(now, timezone_name) is None


def _assert_denied(now: datetime, timezone_name: str = BEIJING) -> None:
    with pytest.raises(BizException) as exc_info:
        _check(now, timezone_name)
    assert exc_info.value.code == ErrorCode.A1001


def test_utc_container_beijing_1000_is_allowed(utc_container):
    """UTC 02:00 == 北京时间 10:00 → 允许外呼（不被 UTC 本地时区带偏）。"""
    _assert_allowed(datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc))


def test_utc_container_beijing_0759_is_denied(utc_container):
    """UTC 前一日 23:59 == 北京时间 07:59 → 未到开始时段。"""
    _assert_denied(datetime(2026, 1, 14, 23, 59, tzinfo=timezone.utc))


def test_utc_container_beijing_2059_is_allowed(utc_container):
    """UTC 12:59 == 北京时间 20:59 → 结束前最后一分钟仍允许。"""
    _assert_allowed(datetime(2026, 1, 15, 12, 59, tzinfo=timezone.utc))


def test_utc_container_beijing_2100_is_denied(utc_container):
    """UTC 13:00 == 北京时间 21:00 → 右开区间，拒绝。"""
    _assert_denied(datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc))


def test_naive_clock_is_interpreted_in_business_timezone(utc_container):
    """注入 naive 时钟时按业务时区解释，而非容器本地时区。"""
    _assert_allowed(datetime(2026, 1, 15, 10, 0))
    _assert_denied(datetime(2026, 1, 15, 22, 0))


def test_business_timezone_drives_decision():
    """同一 UTC 瞬间，注入时区不同则判定不同：02:00Z 在 UTC 为凌晨、在北京为上午。"""
    instant = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
    _assert_denied(instant, timezone_name="UTC")
    _assert_allowed(instant, timezone_name=BEIJING)


def test_live_clock_path_uses_business_timezone(utc_container):
    """真实时钟路径（now=None）产出时区感知时间：不因容器 UTC 抛 naive 相关错误。"""
    # 只验证时区感知路径可执行；具体时刻由 allowed_hours 决定，捕获业务拒绝即可。
    try:
        check_call_time(timezone_name=BEIJING, allowed_hours=ALLOWED_HOURS)
    except BizException as exc:
        assert exc.code == ErrorCode.A1001


def test_invalid_timezone_name_fails_fast():
    """非法时区名不做静默回退，直接抛出配置错误。"""
    with pytest.raises(ZoneInfoNotFoundError):
        _check(datetime(2026, 1, 15, 10, 0), timezone_name="Not/AZone")
