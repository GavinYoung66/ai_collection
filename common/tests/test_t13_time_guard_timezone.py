"""T13：外呼时段判定的时区确定性单测。

核心验证：容器 ``TZ=UTC`` 下，业务时区（CALL_TIMEZONE=Asia/Shanghai）的时段
判定与本地北京时间一致；固定时钟注入，不依赖真实时间。
"""
import time as time_module
from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.utils.time_guard import check_call_time

BEIJING = "Asia/Shanghai"


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


@pytest.fixture(autouse=True)
def _default_business_config(monkeypatch):
    monkeypatch.setattr(settings, "CALL_TIMEZONE", BEIJING)
    monkeypatch.setattr(settings, "CALL_ALLOWED_HOURS", "08:00-21:00")


def _assert_allowed(now: datetime) -> None:
    assert check_call_time(now) is None


def _assert_denied(now: datetime) -> None:
    with pytest.raises(BizException) as exc_info:
        check_call_time(now)
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


def test_business_timezone_drives_decision(monkeypatch):
    """同一 UTC 瞬间，配置时区不同则判定不同：02:00Z 在 UTC 为凌晨、在北京为上午。"""
    instant = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(settings, "CALL_TIMEZONE", "UTC")
    _assert_denied(instant)
    monkeypatch.setattr(settings, "CALL_TIMEZONE", BEIJING)
    _assert_allowed(instant)


def test_live_clock_path_uses_business_timezone(utc_container):
    """真实时钟路径（now=None）产出时区感知时间：不因容器 UTC 抛 naive 相关错误。"""
    # 只验证时区感知路径可执行；具体时刻由 CALL_ALLOWED_HOURS 决定，捕获业务拒绝即可。
    try:
        check_call_time()
    except BizException as exc:
        assert exc.code == ErrorCode.A1001


def test_invalid_timezone_name_fails_fast(monkeypatch):
    """非法 CALL_TIMEZONE 不做静默回退，直接抛出配置错误。"""
    monkeypatch.setattr(settings, "CALL_TIMEZONE", "Not/AZone")
    with pytest.raises(ZoneInfoNotFoundError):
        check_call_time(datetime(2026, 1, 15, 10, 0))
