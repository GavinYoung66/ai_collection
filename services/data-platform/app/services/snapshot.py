"""dw.ads_case_loop 快照读写：一行一 case_id，恰好 14 列。

- GET：只读返回整行，供投诉进程内桥反查 session_id。
- PUT：内部回写口只更新投诉三列（complaint_id/verdict/task_status），
  只更新已存在的行，不新增行、不改其它案件。
不做报表、不做第二张宽表。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 快照表固定 14 列（英文列名），顺序即对外 data 的键顺序。
SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "case_id",
    "customer_name",
    "overdue_amount",
    "overdue_days",
    "session_id",
    "call_ended",
    "qc_record_id",
    "compliance_score",
    "risk_level",
    "has_high_alert",
    "complaint_id",
    "verdict",
    "task_status",
    "backflow_at",
)

_SQL_GET = text(
    """
    SELECT case_id, customer_name, overdue_amount, overdue_days, session_id, call_ended,
           qc_record_id, compliance_score, risk_level, has_high_alert,
           complaint_id, verdict, task_status, backflow_at
    FROM dw.ads_case_loop
    WHERE case_id = :case_id
    """
)

# 只更新该 case_id 行的投诉三列；不 INSERT、不动其它列、不动其它案件。
_SQL_UPDATE_COMPLAINT_COLS = text(
    """
    UPDATE dw.ads_case_loop
    SET complaint_id = :complaint_id,
        verdict      = :verdict,
        task_status  = :task_status
    WHERE case_id = :case_id
    """
)

# 质检列回流：收到 qc.check.completed 后更新质检四列 + backflow_at。
# 事件键映射：score→compliance_score、risk→risk_level、grade 不落库。
# has_high_alert 由 qc_alerts 是否含 level=high 判定（调用方传入）。
_SQL_UPDATE_QC_COLS = text(
    """
    UPDATE dw.ads_case_loop
    SET qc_record_id    = :qc_record_id,
        compliance_score = CAST(:compliance_score AS int),
        risk_level       = CAST(:risk_level AS varchar),
        has_high_alert   = CAST(:has_high_alert AS char(1)),
        backflow_at      = :backflow_at
    WHERE case_id = :case_id
    """
)

# qc_alerts 无 session_id 列（只有 qc_record_id 外键），JOIN qc_records 反查。
# level 列 CHECK 仅允许 'high'，仍显式带上以对齐语义。
_SQL_HAS_HIGH_ALERT = text(
    """
    SELECT EXISTS(
        SELECT 1
        FROM qc_alerts a
        JOIN qc_records q ON q.id = a.qc_record_id
        WHERE q.session_id = :session_id
          AND q.is_delete = '0'
          AND a.level = 'high'
          AND a.is_delete = '0'
    ) AS has_high
    """
)

# 推投诉前确认 qc_records 已落库（真开检，非手插）。
_SQL_QC_RECORD_EXISTS = text(
    """
    SELECT EXISTS(
        SELECT 1 FROM qc_records
        WHERE session_id = :session_id
          AND is_delete = '0'
    ) AS exists_flag
    """
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def serialize_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """整行序列化为 14 键响应；Decimal→float，时间→ISO 字符串，NULL 保留。"""
    data: dict[str, Any] = {}
    for col in SNAPSHOT_COLUMNS:
        value = row.get(col)
        if isinstance(value, Decimal):
            data[col] = float(value)
        elif isinstance(value, datetime):
            data[col] = _iso(value)
        else:
            data[col] = value
    return data


async def get_snapshot(session: AsyncSession, case_id: str) -> dict[str, Any] | None:
    result = await session.execute(_SQL_GET, {"case_id": case_id})
    row = result.mappings().first()
    if row is None:
        return None
    return serialize_snapshot(dict(row))


async def update_complaint_cols(
    session: AsyncSession,
    case_id: str,
    *,
    complaint_id: str,
    verdict: str,
    task_status: str,
) -> bool:
    """内部回写：仅投诉三列。返回 False 表示该行不存在（调用方回 B0004，不补插行）。"""
    result = await session.execute(
        _SQL_UPDATE_COMPLAINT_COLS,
        {
            "case_id": case_id,
            "complaint_id": complaint_id,
            "verdict": verdict,
            "task_status": task_status,
        },
    )
    return (result.rowcount or 0) == 1


async def check_has_high_alert(session: AsyncSession, session_id: str) -> bool:
    """查 qc_alerts 是否存在 level=high 且未删除。事件 payload 没有该字段。

    qc_alerts 无 session_id 列，经 qc_record_id JOIN qc_records 反查。
    """
    result = await session.execute(_SQL_HAS_HIGH_ALERT, {"session_id": session_id})
    row = result.mappings().first()
    return bool(row and row.get("has_high"))


async def qc_record_exists(session: AsyncSession, session_id: str) -> bool:
    """确认 qc_records 已落库（真开检）后才允许推投诉。"""
    result = await session.execute(_SQL_QC_RECORD_EXISTS, {"session_id": session_id})
    row = result.mappings().first()
    return bool(row and row.get("exists_flag"))


async def update_qc_cols(
    session: AsyncSession,
    case_id: str,
    *,
    qc_record_id: str | None,
    compliance_score: int | None,
    risk_level: str | None,
    has_high_alert: str,
    backflow_at: datetime | None = None,
) -> bool:
    """质检列回流：收到 qc.check.completed 后更新质检四列 + backflow_at。

    事件键映射：score→compliance_score、risk→risk_level、grade 不落库。
    has_high_alert 由调用方通过 check_has_high_alert 查 qc_alerts 后传入 '1' 或 '0'。
    返回 False 表示该行不存在。
    """
    if backflow_at is None:
        backflow_at = datetime.now(_SHANGHAI)
    result = await session.execute(
        _SQL_UPDATE_QC_COLS,
        {
            "case_id": case_id,
            "qc_record_id": qc_record_id,
            "compliance_score": compliance_score,
            "risk_level": risk_level,
            "has_high_alert": has_high_alert,
            "backflow_at": backflow_at,
        },
    )
    return (result.rowcount or 0) == 1
