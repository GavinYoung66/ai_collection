"""外呼时段校验（合规参数由调用方注入，common 不承载业务配置）。"""
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


def _business_timezone(timezone_name: str) -> ZoneInfo:
    """业务时区（调用方传入 IANA 名称）；配置非法时调用即失败。"""
    return ZoneInfo(timezone_name)


def check_call_time(
    now: Optional[datetime] = None,
    *,
    timezone_name: str,
    allowed_hours: str,
) -> None:
    """检查当前是否在允许的外呼时段内。

    合规判定绑定调用方传入的 IANA 时区（call-service 配置 ``CALL_TIMEZONE``），
    不读进程本地时区，因此容器 ``TZ=UTC`` 与本地北京时间得到同一结果。
    ``now`` 仅供测试注入固定时钟：naive 值按业务时区解释，aware 值先换算到业务时区。
    """
    tz = _business_timezone(timezone_name)
    current = now if now is not None else datetime.now(tz)
    current = current.replace(tzinfo=tz) if current.tzinfo is None else current.astimezone(tz)

    start_str, end_str = allowed_hours.split("-")  # e.g. "08:00-21:00"

    start_h, start_m = int(start_str.split(":")[0]), int(start_str.split(":")[1])
    end_h, end_m = int(end_str.split(":")[0]), int(end_str.split(":")[1])

    current_minutes = current.hour * 60 + current.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    if not (start_minutes <= current_minutes < end_minutes):
        raise BizException(ErrorCode.A1001)
