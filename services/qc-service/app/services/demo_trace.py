"""演示时间线：本地 Redis 事件 + 可选 LangSmith 外链。无 Key 不影响本地 events。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from common.config.settings import settings as platform_settings
from common.security.mask import mask_text
from common.trace.langfuse_gate import (
    LangfuseRun,
    configure_langfuse_env as _configure_langfuse_env,
)
from common.trace.langsmith_gate import LangSmithRun, langsmith_url

from app.repositories.demo_store import DemoTraceStore

logger = logging.getLogger(__name__)

CALL_QC_NOT_INVOKED = "本通未调用 MCP call_qc"
CALL_QC_STATUS_FIELD = "not_called"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def make_trace_event(
    *,
    category: str,
    name: str,
    status: str = "ok",
    duration_ms: int = 0,
    summary: str = "",
    started_at: datetime | str | None = None,
) -> dict[str, Any]:
    if isinstance(started_at, datetime):
        started = started_at.isoformat()
    elif started_at:
        started = str(started_at)
    else:
        started = utc_now().isoformat()
    return {
        "category": category,
        "name": name,
        "started_at": started,
        "duration_ms": int(duration_ms or 0),
        "status": status,
        "summary": mask_text(summary or ""),
    }


def call_qc_skip_event() -> dict[str, Any]:
    return make_trace_event(
        category="mcp",
        name="call_qc",
        status="skipped",
        duration_ms=0,
        summary=CALL_QC_NOT_INVOKED,
    )


def build_langsmith_url(run_id: str | None) -> str | None:
    """仅当统一开关开启 + run id + URL 模板（含 {run_id} 或 {project}）齐全时返回外链。"""
    return langsmith_url(run_id, platform_settings, fallback_project="qc-service")


async def safe_append(store: DemoTraceStore | None, session_id: str | None, event: dict[str, Any]) -> None:
    if store is None or not session_id:
        return
    try:
        await store.append(session_id, event)
    except Exception:
        logger.debug("demo trace append failed session_id=%s", session_id, exc_info=True)


async def safe_set_meta(store: DemoTraceStore | None, session_id: str | None, **fields: Any) -> None:
    if store is None or not session_id:
        return
    try:
        await store.set_meta(session_id, **fields)
    except Exception:
        logger.debug("demo trace meta failed session_id=%s", session_id, exc_info=True)


def configure_langfuse_env() -> None:
    """启动时按统一开关写入 / 清除 Langfuse SDK 环境变量（转发 common 闸门）。"""
    _configure_langfuse_env(platform_settings)


class OptionalLangSmithRun(LangSmithRun):
    """可选包裹 LLM 复核；统一开关关闭或异常时为空操作，不影响本地时间线。不引入 LangGraph。"""

    def __init__(self, name: str, inputs: dict[str, Any]):
        super().__init__(
            name,
            inputs,
            settings=platform_settings,
            fallback_project="qc-service",
        )


class OptionalLangfuseRun(LangfuseRun):
    """可选包裹 LLM 复核（Langfuse 侧）；统一开关关闭或异常时为空操作。"""

    def __init__(self, name: str, inputs: dict[str, Any]):
        super().__init__(name, inputs, settings=platform_settings)
