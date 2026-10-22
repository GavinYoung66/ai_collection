"""qc_reviews 与待复核 qc_records。"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qc import QcRecord, QcReview


class ReviewRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_record(self, qc_record_id: UUID | str) -> QcRecord | None:
        try:
            rid = qc_record_id if isinstance(qc_record_id, UUID) else UUID(str(qc_record_id))
        except ValueError:
            return None
        stmt = select(QcRecord).where(QcRecord.id == rid, QcRecord.is_delete == "0")
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_pending(self, *, page: int, page_size: int) -> tuple[list[QcRecord], int]:
        filters = (QcRecord.qc_status == "PENDING_REVIEW", QcRecord.is_delete == "0")
        count_stmt = select(func.count()).select_from(QcRecord).where(*filters)
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        stmt = (
            select(QcRecord)
            .where(*filters)
            .order_by(QcRecord.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def insert_review(
        self,
        *,
        qc_record_id: UUID | str,
        action: str,
        revised_score: int | None = None,
        revised_deduction: int | None = None,
        reason: str | None = None,
        reviewer_id: UUID | None = None,
    ) -> QcReview:
        rid = qc_record_id if isinstance(qc_record_id, UUID) else UUID(str(qc_record_id))
        row = QcReview(
            id=uuid4(),
            qc_record_id=rid,
            action=action,
            revised_score=revised_score,
            revised_deduction=revised_deduction,
            reason=reason,
            reviewer_id=reviewer_id,
        )
        self.session.add(row)
        await self.session.flush()
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        return row

    @staticmethod
    def pending_item(row: QcRecord) -> dict[str, Any]:
        return {
            "qc_record_id": str(row.id),
            "session_id": row.session_id,
            "case_id": str(row.case_id) if row.case_id is not None else None,
            "compliance_score": row.compliance_score,
            "deduction_total": row.deduction_total,
            "risk": row.risk_level,
            "grade": row.grade,
            "qc_result": row.qc_result,
            "qc_status": row.qc_status,
        }
