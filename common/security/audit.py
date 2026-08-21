"""审计日志工具。"""
import structlog

logger = structlog.get_logger()


def log_action(action: str, user_id: str | None = None, resource: str = "", trace_id: str = "", **extra) -> None:
    logger.info(
        "audit",
        action=action,
        user_id=user_id,
        resource=resource,
        trace_id=trace_id,
        **extra,
    )
