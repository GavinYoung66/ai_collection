"""演示时间线：本地 Redis 事件 + 可选 LangSmith 外链。无 Key 不影响本地 events。"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from common.security.mask import mask_text

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
    """仅当 Key + run id + URL 模板（含 {run_id} 或 {project}）齐全时返回外链。"""
    if not run_id:
        return None
    key = (os.environ.get("LANGSMITH_API_KEY") or "").strip()
    if not key:
        return None
    tmpl = (os.environ.get("LANGSMITH_TRACE_URL_TEMPLATE") or "").strip()
    if not tmpl:
        return None
    if "{run_id}" not in tmpl and "{project}" not in tmpl:
        return None
    project = (os.environ.get("LANGSMITH_PROJECT") or "qc-service").strip() or "qc-service"
    try:
        return tmpl.format(run_id=run_id, project=project)
    except Exception:
        return None


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


class OptionalLangSmithRun:
    """可选包裹 LLM 复核；失败不影响本地时间线。不引入 LangGraph。"""

    def __init__(self, name: str, inputs: dict[str, Any]):
        self._root = None
        self.run_id: str | None = None
        try:
            key = (os.environ.get("LANGSMITH_API_KEY") or "").strip()
            if not key:
                return
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            os.environ["LANGSMITH_API_KEY"] = key
            project = (os.environ.get("LANGSMITH_PROJECT") or "qc-service").strip() or "qc-service"
            from langsmith.run_trees import RunTree

            self._root = RunTree(
                name=name,
                run_type="chain",
                inputs=inputs,
                project_name=project,
            )
            self._root.post()
            self.run_id = str(getattr(self._root, "id", "") or "") or None
        except Exception:
            self._root = None
            self.run_id = None

    def end(self, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
        if self._root is None:
            return
        try:
            if error:
                self._root.end(error=error)
            else:
                self._root.end(outputs=outputs or {})
            self._root.patch()
        except Exception:
            pass
