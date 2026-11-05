"""V4 有序证据清单：trace_events 为主，辅以 regulation_refs / agent_logs，按时间升序。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.service.security import mask_before_persist
from common.utils.time_parse import BUSINESS_TZ, parse_naive_or_aware

logger = structlog.get_logger()

_CATEGORY_RANK = {
    "MCP": 0,
    "RAG": 1,
    "Agent": 2,
    "LLM": 3,
    "其他": 9,
}


def _ts_key(started_at: Any) -> float:
    """T82：时间线排序键——naive 统一绑业务时区（Asia/Shanghai，time_parse 单源）。

    消除按进程本地时区 ``.timestamp()`` 造成的判责证据时间线 8 小时错位；
    None / 非法时间值排最后（不影响可解析条目的相对顺序）。
    """
    if started_at is None:
        return float("inf")
    try:
        return parse_naive_or_aware(started_at, default_tz=BUSINESS_TZ).timestamp()
    except Exception as e:
        # W4/9.6：静默吞异常留痕（debug 级：非法时间值排最后，业务允许忽略）
        logger.debug("ordered_evidence_ts_parse_failed", error_type=type(e).__name__, exc_info=True)
        return float("inf")


def _append_item(
    items: list[dict[str, Any]],
    *,
    category: str,
    name: str,
    summary: str,
    started_at: Any = None,
    status: str = "success",
) -> None:
    if isinstance(started_at, datetime):
        started_out: Any = started_at.isoformat()
    else:
        started_out = started_at
    items.append(
        {
            "category": category,
            "name": name,
            "summary": mask_before_persist(str(summary or "")),
            "started_at": started_out,
            "status": status,
        }
    )


def build_ordered_evidence(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """聚合脱敏有序证据：trace_events → regulation/rag refs → agent_logs（补薄）。"""
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(**kwargs: Any) -> None:
        key = (
            str(kwargs.get("category") or ""),
            str(kwargs.get("name") or ""),
            str(kwargs.get("summary") or "")[:120],
        )
        if key in seen:
            return
        seen.add(key)
        _append_item(items, **kwargs)

    for ev in list(rec.get("trace_events") or []):
        if not isinstance(ev, dict):
            continue
        _add(
            category=str(ev.get("category") or "其他"),
            name=str(ev.get("name") or "—"),
            summary=str(ev.get("summary") or ""),
            started_at=ev.get("started_at"),
            status=str(ev.get("status") or "success"),
        )

    for rid in list(rec.get("regulation_refs") or []) + list(rec.get("rag_source_ids") or []):
        if not rid:
            continue
        _add(
            category="RAG",
            name=str(rid),
            summary=f"法规/知识库引用 {rid}",
            status="success",
        )

    # trace 偏薄时用 agent_logs 补证据摘要（脱敏已在落盘时处理）
    if len(items) < 2:
        for log in list(rec.get("agent_logs") or []):
            if not isinstance(log, dict):
                continue
            node = str(log.get("node_name") or "agent")
            out = str(log.get("output_summary") or log.get("input_summary") or "")
            if not out:
                continue
            _add(
                category="Agent",
                name=node,
                summary=out[:400],
                started_at=log.get("started_at"),
                status="success",
            )

    items.sort(
        key=lambda it: (
            _ts_key(it.get("started_at")),
            _CATEGORY_RANK.get(str(it.get("category") or "其他"), 5),
            str(it.get("name") or ""),
        )
    )
    for i, it in enumerate(items, start=1):
        it["ord"] = i
    return items
