"""投诉侧按 case_id 反查并组装差异化 complaint_text（tasks 5.1 / 5.2）。"""
from __future__ import annotations

from typing import Any

import httpx
import structlog
from sqlalchemy import text

from common.complaint_text import FALLBACK_COMPLAINT_TEXT, build_complaint_text
from common.db.session import async_session_factory

from app.config import settings

logger = structlog.get_logger()

_SQL_VIOLATIONS_BY_SESSION = text(
    """
    SELECT v.rule_id, v.speaker, v.severity, v.redline_tag, v.evidence_text
    FROM qc_violations v
    JOIN qc_records q ON q.id = v.qc_record_id
    WHERE q.session_id = :session_id
      AND q.is_delete = '0'
      AND v.is_delete = '0'
    ORDER BY v.create_time
    """
)


async def _fetch_snapshot_ctx(case_id: str) -> tuple[str, str, str]:
    """HTTP GET DP 快照：session_id / customer_name / overdue_amount。"""
    base = (getattr(settings, "DATA_PLATFORM_URL", "") or "").rstrip("/")
    if not base:
        return "", "客户", "未知"
    url = f"{base}/api/v1/data/snapshot/{case_id}"
    try:
        timeout = float(getattr(settings, "BRIDGE_HTTP_TIMEOUT", 5.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(
                "complaint_text_snapshot_http_miss",
                case_id=case_id,
                status=resp.status_code,
            )
            return "", "客户", "未知"
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return "", "客户", "未知"
        sid = str(data.get("session_id") or "")
        customer = str(data.get("customer_name") or "客户")
        overdue = data.get("overdue_amount")
        overdue_s = str(overdue) if overdue is not None else "未知"
        return sid, customer, overdue_s
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "complaint_text_snapshot_fetch_failed",
            case_id=case_id,
            error=str(exc),
        )
        return "", "客户", "未知"


async def _fetch_violations(session_id: str) -> list[dict[str, Any]]:
    if not session_id:
        return []
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                _SQL_VIOLATIONS_BY_SESSION, {"session_id": session_id}
            )
            return [dict(r) for r in result.mappings().all()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "complaint_text_violations_fetch_failed",
            session_id=session_id,
            error=str(exc),
        )
        return []


async def assemble_complaint_text_for_case(case_id: str) -> tuple[str, str, str]:
    """按 case_id 组装正文。

    Returns:
        (complaint_text, category, session_id)
    """
    session_id, customer, overdue = await _fetch_snapshot_ctx(case_id)
    if not session_id:
        logger.warning("complaint_text_no_session", case_id=case_id)
        return FALLBACK_COMPLAINT_TEXT, "fallback", ""
    violations = await _fetch_violations(session_id)
    category, text_body = build_complaint_text(
        violations=violations, customer=customer, overdue_amount=overdue
    )
    logger.info(
        "complaint_text_assembled",
        case_id=case_id,
        session_id=session_id,
        category=category,
        violation_count=len(violations),
    )
    return text_body, category, session_id
