"""structlog 结构化 JSON 日志 + 脱敏过滤器。"""
import logging
import re

import structlog

_PHONE_RE = re.compile(r"1[3-9]\d{9}")


def _mask_processor(_, __, event_dict):
    """脱敏处理器：日志中的手机号中间 4 位替换为 ****。"""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = _PHONE_RE.sub(
                lambda m: m.group()[:3] + "****" + m.group()[7:], value
            )
    return event_dict


def setup_logging(env: str = "dev", level: str | None = None) -> None:
    """初始化 structlog。

    - ``level``：显式日志级别名（如 DEBUG/INFO/WARN/ERROR），优先于 env 推断。
    - 未传 ``level`` 时：``env=="dev"`` → DEBUG，否则 INFO。
    """
    if level:
        log_level = getattr(logging, str(level).upper(), None)
        if not isinstance(log_level, int):
            # WARN 在 stdlib 为 WARNING
            log_level = logging.WARNING if str(level).upper() == "WARN" else logging.INFO
    else:
        log_level = logging.DEBUG if env == "dev" else logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _mask_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
