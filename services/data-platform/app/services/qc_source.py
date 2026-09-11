"""M1 只读 qc-source / pending-qc：JOIN public.call_records + dialog_turns + cases。

禁止 dw/ODS、禁止宽表、禁止回调 qc。无 phone 解密则省略 dial_history_24h。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SHANGHAI = ZoneInfo("Asia/Shanghai")

_SQL_CALL = text(
    """
    SELECT
        cr.id AS call_id,
        cr.session_id,
        cr.case_id,
        cr.call_type,
        cr.call_status,
        cr.start_time,
        cr.end_time,
        cr.duration_seconds,
        cr.result,
        c.customer_name,
        c.overdue_amount,
        c.overdue_days
    FROM call_records cr
    LEFT JOIN cases c
      ON c.id = cr.case_id AND c.is_delete = '0'
    WHERE cr.session_id = :session_id
      AND cr.is_delete = '0'
    LIMIT 1
    """
)

_SQL_TURNS = text(
    """
    SELECT id, turn_number, speaker, text
    FROM dialog_turns
    WHERE session_id = :session_id
    ORDER BY turn_number ASC
    """
)

_SQL_PENDING = text(
    """
    SELECT cr.session_id
    FROM call_records cr
    WHERE cr.is_delete = '0'
      AND cr.call_status = 'completed'
      AND cr.end_time IS NOT NULL
      AND ((cr.end_time AT TIME ZONE 'Asia/Shanghai')::date) = :etl_date
      AND EXISTS (
            SELECT 1 FROM dialog_turns dt WHERE dt.session_id = cr.session_id
      )
      AND (
            NOT EXISTS (
                SELECT 1 FROM qc_records q
                WHERE q.session_id = cr.session_id AND q.is_delete = '0'
            )
            OR EXISTS (
                SELECT 1 FROM qc_records q
                WHERE q.session_id = cr.session_id
                  AND q.is_delete = '0'
                  AND q.qc_status = 'PARTIAL'
            )
      )
    ORDER BY cr.end_time DESC
    LIMIT :lim
    """
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_SHANGHAI).isoformat()


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def map_speaker(raw: str | None) -> str:
    if raw in {"agent", "user"}:
        return raw
    if raw in {"ai", "human"}:
        return "agent"
    return "agent"


def assemble_qc_source(*, call: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    """组装 QCSession 同构 data。result 必须出键；M1 恒 null ptp/standard_answer；无 24h 则省略。"""
    mapped_turns: list[dict[str, Any]] = []
    for row in turns:
        turn_number = int(row.get("turn_number") or 0)
        mapped_turns.append(
            {
                "turn_id": f"t{turn_number}",
                "speaker": map_speaker(row.get("speaker")),
                "text": str(row.get("text") or ""),
                "turn_index": turn_number,
            }
        )
    call_status = call.get("call_status")
    end_time = call.get("end_time")
    case_id = call.get("case_id")
    call_id = call.get("call_id")
    payload: dict[str, Any] = {
        "text_ready": bool(mapped_turns),
        "call_status": call_status,
        "session_id": call.get("session_id"),
        "case_id": str(case_id) if case_id is not None else None,
        "call_id": str(call_id) if call_id is not None else None,
        "call_ended": call_status == "completed" and end_time is not None,
        "agent_type": call.get("call_type"),
        "started_at": _iso(call.get("start_time")),
        "ended_at": _iso(end_time),
        "duration_seconds": call.get("duration_seconds"),
        "result": call.get("result"),
        "debtor_name": call.get("customer_name"),
        "turns": mapped_turns,
        "case_snapshot": {
            "due_amount": _num(call.get("overdue_amount")),
            "overdue_days": call.get("overdue_days"),
            "ptp_amount": None,
            "strategy_refs": [],
            "standard_answer_hit": None,
        },
    }
    return payload


async def get_qc_source(session: AsyncSession, session_id: str) -> dict[str, Any] | None:
    call_res = await session.execute(_SQL_CALL, {"session_id": session_id})
    call = call_res.mappings().first()
    if call is None:
        return None
    turns_res = await session.execute(_SQL_TURNS, {"session_id": session_id})
    turns = [dict(row) for row in turns_res.mappings().all()]
    return assemble_qc_source(call=dict(call), turns=turns)


async def list_pending_session_ids(
    session: AsyncSession,
    *,
    etl_date: date,
    limit: int,
) -> list[str]:
    capped = min(max(int(limit), 1), 500)
    result = await session.execute(_SQL_PENDING, {"etl_date": etl_date, "lim": capped})
    return [str(row[0]) for row in result.all() if row[0]]
