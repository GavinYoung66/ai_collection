"""时间解析单一事实源（T51）：统一全仓 naive 时间解释为业务时区。

约定（spec §6.2 钉死）：仓库内 naive datetime 一律按业务时区
（``Asia/Shanghai``）解释；aware datetime 原样返回。
禁止消费点自行实现第二份解析（6.3/9.4/9.5 收敛消费本模块）。
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def parse_naive_or_aware(
    value: datetime | str, *, default_tz: tzinfo = BUSINESS_TZ
) -> datetime:
    """将 naive/aware datetime 或 ISO 字符串收敛为 aware datetime。

    - naive datetime：绑定 ``default_tz``（业务时区，Asia/Shanghai）；
    - aware datetime：原样返回；
    - ISO 字符串：容忍 ``Z``/``z`` 后缀（按 UTC 解释）；非法串抛 ``ValueError``；
    - 其他类型：抛 ``ValueError``。
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=default_tz)
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"非法 ISO 时间字符串: {value!r}") from None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=default_tz)
        return parsed
    raise ValueError(f"不支持的时间类型: {type(value).__name__}")


def parse_epoch_or_datetime(
    value: datetime | str | int | float | None,
    *,
    default_tz: tzinfo = BUSINESS_TZ,
) -> datetime | None:
    """存储层时间戳防御解析（T51）：datetime/ISO 串/float epoch 一律收敛为 aware。

    - ``int``/``float``：按 epoch 秒（UTC）解释——兼容残留 float 旧数据，
      不崩且不误删（design D3.1）；bool 不接受；
    - ``datetime``/``str``：委托 :func:`parse_naive_or_aware`；
    - ``None``：原样返回 ``None``（调用方保守跳过，MUST NOT 以 0 参与比较）；
    - 其他类型/非法串：抛 ``ValueError``（调用方按既有异常处理链消费）。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("不支持的时间类型: bool")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, (datetime, str)):
        return parse_naive_or_aware(value, default_tz=default_tz)
    raise ValueError(f"不支持的时间类型: {type(value).__name__}")