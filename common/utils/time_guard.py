"""外呼时段校验。"""
from datetime import datetime, timezone

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


def check_call_time() -> None:
    """检查当前是否在允许的外呼时段内。"""
    now = datetime.now()
    allowed = settings.CALL_ALLOWED_HOURS  # e.g. "08:00-21:00"
    start_str, end_str = allowed.split("-")

    start_h, start_m = int(start_str.split(":")[0]), int(start_str.split(":")[1])
    end_h, end_m = int(end_str.split(":")[0]), int(end_str.split(":")[1])

    current_minutes = now.hour * 60 + now.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    if not (start_minutes <= current_minutes < end_minutes):
        raise BizException(ErrorCode.A1001)
