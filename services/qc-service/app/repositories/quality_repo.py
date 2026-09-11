"""qc_records / qc_violations 仓储。WAIT_TEXT 禁止 INSERT；skipped_rules 不落列。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qc import REDLINE_TAG_VALUES, QcRecord, QcViolation
from app.repositories.job_store import QcJobStore
from common.db.models.audit import AuditLog
from common.security.mask import mask_text
from common.trace.middleware import get_trace_id

SKIP_ACTION = "qc.skip.recorded"
SKIP_RESOURCE_TYPE = "qc_records"


class QualityRepo:
    def __init__(self, session: AsyncSession, job_store: QcJobStore | None = None):
        self.session = session
        self.job_store = job_store

    async def insert_record(
        self,
        *,
        session_id: str,
        compliance_score: int,
        risk_level: str,
        qc_result: str,
        qc_status: str,
        grade: str | None = None,
        case_id: UUID | None = None,
        agent_id: UUID | None = None,
        high_count: int = 0,
        med_count: int = 0,
        low_count: int = 0,
    ) -> QcRecord:
        QcRecord.assert_db_status(qc_status)
        row = QcRecord(
            session_id=session_id,
            compliance_score=compliance_score,
            risk_level=risk_level,
            qc_result=qc_result,
            qc_status=qc_status,
            grade=grade,
            case_id=case_id,
            agent_id=agent_id,
            high_count=high_count,
            med_count=med_count,
            low_count=low_count,
        )
        self.session.add(row)
        await self.session.flush()
        if getattr(row, "id", None) is None:
            from uuid import uuid4

            row.id = uuid4()
        return row

    async def add_violation(self, record: QcRecord, **fields: Any) -> QcViolation:
        evidence = fields.get("evidence_text")
        if evidence:
            fields["evidence_text"] = mask_text(str(evidence))
        tag = fields.get("redline_tag")
        if tag is not None and tag not in REDLINE_TAG_VALUES:
            fields["redline_tag"] = None
        row = QcViolation(qc_record_id=record.id, **fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def record_skipped_rules(
        self,
        *,
        qc_record_id: UUID | str,
        skipped_rules: Sequence[str] | None = None,
    ) -> AuditLog:
        """终态写 audit_logs；空数组也写。不加 qc_records.skipped_rules 列。"""
        rules = list(skipped_rules or [])
        log = AuditLog(
            trace_id=get_trace_id() or None,
            user_id=None,
            action=SKIP_ACTION,
            resource_type=SKIP_RESOURCE_TYPE,
            resource_id=str(qc_record_id),
            detail={"skipped_rules": rules, "actor": "system"},
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_by_session_id(self, session_id: str) -> QcRecord | None:
        stmt = select(QcRecord).where(QcRecord.session_id == session_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def call_record_exists(self, session_id: str) -> bool:
        from sqlalchemy import text

        result = await self.session.execute(
            text("SELECT 1 FROM call_records WHERE session_id = :sid LIMIT 1"),
            {"sid": session_id},
        )
        return result.scalar() is not None

    async def load_skipped_rules(
        self,
        *,
        session_id: str,
        qc_record_id: UUID | str,
    ) -> list[str]:
        """GET 详情回源：Redis qc:job（仅 DONE）→ audit_logs → []。"""
        if self.job_store is not None:
            job = await self.job_store.get_job(session_id)
            if job is not None and job.get("status") == "DONE" and "skipped_rules" in job:
                return list(job.get("skipped_rules") or [])
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.action == SKIP_ACTION,
                AuditLog.resource_type == SKIP_RESOURCE_TYPE,
                AuditLog.resource_id == str(qc_record_id),
            )
            .order_by(AuditLog.create_time.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None or not row.detail:
            return []
        return list(row.detail.get("skipped_rules") or [])

    def _record_filters(
        self,
        *,
        agent_id: UUID | None = None,
        risk: str | None = None,
        grade: str | None = None,
        qc_status: str | None = None,
        qc_result: str | None = None,
        case_id: UUID | None = None,
        session_id: str | None = None,
        create_time_from: datetime | None = None,
        create_time_to: datetime | None = None,
    ) -> list:
        conds = [QcRecord.is_delete == "0"]
        if agent_id is not None:
            conds.append(QcRecord.agent_id == agent_id)
        if risk is not None:
            conds.append(QcRecord.risk_level == risk)
        if grade is not None:
            conds.append(QcRecord.grade == grade)
        if qc_status is not None:
            conds.append(QcRecord.qc_status == qc_status)
        if qc_result is not None:
            conds.append(QcRecord.qc_result == qc_result)
        if case_id is not None:
            conds.append(QcRecord.case_id == case_id)
        if session_id is not None:
            conds.append(QcRecord.session_id == session_id)
        if create_time_from is not None:
            conds.append(QcRecord.create_time >= create_time_from)
        if create_time_to is not None:
            conds.append(QcRecord.create_time <= create_time_to)
        return conds

    async def list_records(
        self,
        *,
        page: int,
        page_size: int,
        agent_id: UUID | None = None,
        risk: str | None = None,
        grade: str | None = None,
        qc_status: str | None = None,
        qc_result: str | None = None,
        case_id: UUID | None = None,
        session_id: str | None = None,
        create_time_from: datetime | None = None,
        create_time_to: datetime | None = None,
    ) -> tuple[list[QcRecord], int]:
        conds = self._record_filters(
            agent_id=agent_id,
            risk=risk,
            grade=grade,
            qc_status=qc_status,
            qc_result=qc_result,
            case_id=case_id,
            session_id=session_id,
            create_time_from=create_time_from,
            create_time_to=create_time_to,
        )
        where = and_(*conds)
        total = int((await self.session.execute(select(func.count()).select_from(QcRecord).where(where))).scalar_one())
        offset = (page - 1) * page_size
        stmt = (
            select(QcRecord)
            .where(where)
            .order_by(QcRecord.create_time.desc())
            .offset(offset)
            .limit(page_size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def list_violations(self, qc_record_id: UUID | str) -> list[QcViolation]:
        stmt = (
            select(QcViolation)
            .where(
                QcViolation.qc_record_id == qc_record_id,
                QcViolation.is_delete == "0",
            )
            .order_by(QcViolation.create_time.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def stats(
        self,
        *,
        group_by: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        conds = [QcRecord.is_delete == "0"]
        if from_ts is not None:
            conds.append(QcRecord.create_time >= from_ts)
        if to_ts is not None:
            conds.append(QcRecord.create_time <= to_ts)
        where = and_(*conds)
        if group_by == "agent":
            stmt = (
                select(QcRecord.agent_id.label("key"), func.count().label("count"))
                .where(where)
                .group_by(QcRecord.agent_id)
                .order_by(func.count().desc())
            )
            rows = (await self.session.execute(stmt)).all()
            return [{"key": str(row.key) if row.key is not None else "", "count": int(row.count)} for row in rows]
        if group_by == "day":
            day_col = func.date_trunc("day", QcRecord.create_time)
            stmt = (
                select(day_col.label("key"), func.count().label("count"))
                .where(where)
                .group_by(day_col)
                .order_by(day_col.asc())
            )
            rows = (await self.session.execute(stmt)).all()
            items = []
            for row in rows:
                key = row.key.isoformat() if hasattr(row.key, "isoformat") else str(row.key)
                items.append({"key": key, "count": int(row.count)})
            return items
        stmt = (
            select(
                QcViolation.rule_id.label("key"),
                func.count().label("count"),
                func.coalesce(
                    func.sum(case((QcViolation.severity == "high", 1), else_=0)),
                    0,
                ).label("high_count"),
            )
            .join(QcRecord, QcRecord.id == QcViolation.qc_record_id)
            .where(where, QcViolation.is_delete == "0")
            .group_by(QcViolation.rule_id)
            .order_by(func.count().desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {"key": str(row.key or ""), "count": int(row.count), "high_count": int(row.high_count)}
            for row in rows
        ]
