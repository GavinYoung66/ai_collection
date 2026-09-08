"""研判链路追踪：落单事件构造 + 可选 LangSmith 外链。

本地时间线为 P0；LangSmith 仅为 P1 外链，缺 Key/run id 时返回 null。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.service.security import mask_before_persist


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_trace_event(
    *,
    category: str,
    name: str,
    status: str = "success",
    duration_ms: int = 0,
    summary: str = "",
    started_at: Optional[datetime] = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "name": name,
        "started_at": started_at or utc_now(),
        "duration_ms": int(duration_ms or 0),
        "status": status,
        "summary": mask_before_persist(summary or ""),
    }


def _entry_started_at(entry: dict[str, Any]) -> Optional[datetime]:
    """Prefer audit wall-clock start; fall back to ended_at - latency."""
    started = entry.get("started_at")
    if isinstance(started, datetime):
        return started
    ended = entry.get("ended_at")
    latency_ms = int(entry.get("latency_ms") or 0)
    if isinstance(ended, datetime):
        from datetime import timedelta

        return ended - timedelta(milliseconds=max(latency_ms, 0))
    return None


def mcp_entry_to_event(entry: dict[str, Any]) -> dict[str, Any]:
    server = str(entry.get("server") or "")
    tool = str(entry.get("tool") or "")
    category = "RAG" if server == "rag_kb" else "MCP"
    err = entry.get("error")
    status = str(entry.get("status") or ("error" if err else "success"))
    summary = f"{server}.{tool}"
    if err:
        summary = f"{summary}: {err}"
    elif entry.get("params") is not None:
        summary = f"{summary} params={entry.get('params')}"
    return make_trace_event(
        category=category,
        name=f"{server}.{tool}" if server else tool or "mcp",
        status=status,
        duration_ms=int(entry.get("latency_ms") or 0),
        summary=summary,
        started_at=_entry_started_at(entry),
    )


def llm_entry_to_event(entry: dict[str, Any]) -> dict[str, Any]:
    role = str(entry.get("agent_role") or "").strip()
    name = role or "llm"
    err = entry.get("error")
    status = str(entry.get("status") or ("error" if err else "success"))
    path = entry.get("provider_path") or ""
    preview = entry.get("response_preview") or entry.get("prompt_preview") or ""
    summary = f"path={path}"
    if err:
        summary = f"{summary}; error={err}"
    elif preview:
        summary = f"{summary}; {preview}"
    return make_trace_event(
        category="LLM",
        name=name,
        status=status,
        duration_ms=int(entry.get("latency_ms") or 0),
        summary=summary,
        started_at=_entry_started_at(entry),
    )


def filter_new_logs(
    logs: list[dict[str, Any]],
    *,
    start_index: int,
    session_id: str,
    trace_id: str,
) -> list[dict[str, Any]]:
    """取本轮新增条目，并优先按 session/trace 过滤（防并发串单）。"""
    sliced = list(logs[start_index:])
    if not sliced:
        return []
    matched = []
    for e in sliced:
        sid = e.get("session_id") or ""
        tid = e.get("trace_id") or ""
        if sid and session_id and sid == session_id:
            matched.append(e)
        elif tid and trace_id and tid == trace_id:
            matched.append(e)
        elif not sid and not tid:
            # mock / 未填关联字段：仍归本轮切片
            matched.append(e)
    return matched if matched else sliced


def snapshot_call_log_len(obj: Any) -> int:
    try:
        return len(obj.call_log)
    except Exception:  # noqa: BLE001
        return 0


def build_langsmith_url(run_id: Optional[str]) -> Optional[str]:
    """有外部 run id、Key 且配置了 URL 模板时返回外链；否则 None（避免错误 404 链）。

    必须设置 `LANGSMITH_TRACE_URL_TEMPLATE`（env 或 settings），占位符：
    `{run_id}` / `{project}`。无模板时即使有 run id 也隐藏按钮。
    """
    if not run_id:
        return None
    try:
        from app.config import settings
    except Exception:  # noqa: BLE001
        return None
    key = (getattr(settings, "LANGSMITH_API_KEY", "") or "").strip()
    enabled = bool(getattr(settings, "LANGSMITH_ENABLED", False))
    if not enabled or not key:
        return None
    project = getattr(settings, "LANGSMITH_PROJECT", "complaint-service") or "complaint-service"
    tmpl = (
        (os.environ.get("LANGSMITH_TRACE_URL_TEMPLATE") or "").strip()
        or (getattr(settings, "LANGSMITH_TRACE_URL_TEMPLATE", "") or "").strip()
    )
    if not tmpl:
        return None
    try:
        return tmpl.format(run_id=run_id, project=project)
    except Exception:  # noqa: BLE001
        return None


class OptionalLangSmithRun:
    """可选包裹一次 judge 图执行；失败不影响本地时间线。"""

    def __init__(self, name: str, inputs: dict[str, Any]):
        self._root = None
        self.run_id: Optional[str] = None
        try:
            from app.config import settings

            if not (getattr(settings, "LANGSMITH_ENABLED", False) and getattr(settings, "LANGSMITH_API_KEY", "")):
                return
            # 写入进程环境，供 SDK 读取（不落 git）
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
            os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
            if getattr(settings, "LANGSMITH_PROJECT", ""):
                os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
            from langsmith.run_trees import RunTree

            self._root = RunTree(
                name=name,
                run_type="chain",
                inputs=inputs,
                project_name=getattr(settings, "LANGSMITH_PROJECT", None) or "complaint-service",
            )
            self._root.post()
            self.run_id = str(getattr(self._root, "id", "") or "") or None
        except Exception:  # noqa: BLE001
            self._root = None
            self.run_id = None

    def end(self, outputs: Optional[dict[str, Any]] = None, error: Optional[str] = None) -> None:
        if self._root is None:
            return
        try:
            if error:
                self._root.end(error=error)
            else:
                self._root.end(outputs=outputs or {})
            self._root.patch()
        except Exception:  # noqa: BLE001
            pass


class NodeTimer:
    """相邻节点耗时粗估。"""

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def lap_ms(self) -> int:
        now = time.monotonic()
        ms = int((now - self._t0) * 1000)
        self._t0 = now
        return ms
