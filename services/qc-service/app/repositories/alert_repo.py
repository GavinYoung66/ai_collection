"""qc_alerts 写入与查询。仅 AlertService 资格行落库；不 PUBLISH。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
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
        """T80：幂等 ack——条件更新保护审计字段（复用 6.2 模式，design D3.3）。

        WHERE status != 'ACKED'：rowcount=0 表示并发/重复 ack（他方已处理），
        既有 acked_by/acked_at MUST NOT 被覆盖，幂等读回原记录返回。
        """
        if row.status == "ACKED":
            return row  # 快路径幂等：审计字段不动
        acked_at = datetime.now(timezone.utc)
        stmt = (
            update(QcAlert)
            .where(QcAlert.id == row.id, QcAlert.status != "ACKED")
            .values(status="ACKED", acked_by=acked_by, acked_at=acked_at)
        )
        result = await self.session.execute(stmt)
        if getattr(result, "rowcount", 0) == 0:
            # 并发窗口：他方已 ACKED——读回 DB 最新状态（不覆盖审计字段）
            refresh = getattr(self.session, "refresh", None)
            if refresh is not None:
                await refresh(row)
            return row
        row.status = "ACKED"
        row.acked_by = acked_by
        row.acked_at = acked_at
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
