"""qc_alerts 写入与查询。仅 AlertService 资格行落库；不 PUBLISH。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qc import QcAlert, QcRecord, QcViolation
from common.security.mask import mask_text


class AlertRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_high(
        self,
        *,
        qc_record_id: UUID | str,
        violation_id: UUID | str | None = None,
        alert_id: UUID | str | None = None,
    ) -> QcAlert:
        rid = qc_record_id if isinstance(qc_record_id, UUID) else UUID(str(qc_record_id))
        vid = None
        if violation_id is not None:
            vid = violation_id if isinstance(violation_id, UUID) else UUID(str(violation_id))
        aid = None
        if alert_id is not None:
            aid = alert_id if isinstance(alert_id, UUID) else UUID(str(alert_id))
        row = QcAlert(
            id=aid or uuid4(),
            qc_record_id=rid,
            violation_id=vid,
            level="high",
            status="NEW",
        )
        self.session.add(row)
        await self.session.flush()
        if getattr(row, "id", None) is None:
            row.id = aid or uuid4()
        return row

    async def get(self, alert_id: UUID | str) -> QcAlert | None:
        try:
            aid = alert_id if isinstance(alert_id, UUID) else UUID(str(alert_id))
        except ValueError:
            return None
        stmt = select(QcAlert).where(QcAlert.id == aid, QcAlert.is_delete == "0")
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_alerts(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        stmt = (
            select(QcAlert, QcRecord.session_id, QcViolation.evidence_text)
            .join(QcRecord, QcAlert.qc_record_id == QcRecord.id)
            .outerjoin(QcViolation, QcAlert.violation_id == QcViolation.id)
            .where(QcAlert.is_delete == "0")
        )
        if status:
            stmt = stmt.where(QcAlert.status == status)
        if session_id:
            stmt = stmt.where(QcRecord.session_id == session_id)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        stmt = stmt.order_by(QcAlert.create_time.desc()).offset((page - 1) * page_size).limit(page_size)
        rows = (await self.session.execute(stmt)).all()
        items = [self._item(alert, session_id=sid, evidence=evidence) for alert, sid, evidence in rows]
        return items, total

    async def ack(self, row: QcAlert, *, acked_by: UUID | None) -> QcAlert:
        row.status = "ACKED"
        row.acked_by = acked_by
        row.acked_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    @staticmethod
    def _item(row: QcAlert, *, session_id: str | None, evidence: str | None) -> dict[str, Any]:
        created = getattr(row, "create_time", None)
        return {
            "alert_id": str(row.id),
            "qc_record_id": str(row.qc_record_id),
            "session_id": session_id,
            "level": row.level,
            "status": row.status,
            "evidence": mask_text(evidence or ""),
            "create_time": created.isoformat() if created is not None else None,
        }
