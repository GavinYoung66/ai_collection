"""人工复核：sample_type 映射、落 qc_reviews、发布 qc.review.done。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.events.publisher import QcEventPublisher, sample_type_for
from app.models.qc import QcRecord
from app.repositories.review_repo import ReviewRepo
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.security.mask import mask_text

ALLOWED_ACTIONS = frozenset({"CONFIRM", "DISMISS", "CORRECT"})


class ReviewService:
    def __init__(
        self,
        publisher: QcEventPublisher | None = None,
        *,
        repo: ReviewRepo | None = None,
    ) -> None:
        self.publisher = publisher or QcEventPublisher()
        self.repo = repo

    @staticmethod
    def sample_type(action: str) -> str:
        return sample_type_for(action)

    async def publish_done(
        self,
        *,
        qc_record_id: str,
        action: str,
        revised_deduction: int | None = None,
        revised_score: int | None = None,
        redline_tags: list[str] | None = None,
        evidence_masked: str | None = None,
    ) -> dict[str, Any]:
        return await self.publisher.publish_review_done(
            qc_record_id=qc_record_id,
            action=action,
            revised_deduction=revised_deduction,
            revised_score=revised_score,
            redline_tags=redline_tags,
            evidence_masked=evidence_masked,
        )

    async def submit(
        self,
        record: QcRecord | None,
        *,
        action: str,
        revised_deduction: int | None = None,
        revised_score: int | None = None,
        reason: str | None = None,
        reviewer_id: UUID | None = None,
    ) -> dict[str, Any]:
        if record is None or record.qc_status == "REVIEWED":
            raise BizException(ErrorCode.A2007)
        if action not in ALLOWED_ACTIONS:
            raise BizException(ErrorCode.A2002)
        deduction_to_write: int | None = None
        if action == "CORRECT":
            deduction_to_write = self._resolve_correct_deduction(
                revised_deduction, revised_score
            )
        record.qc_status = "REVIEWED"
        self._finalize_reviewed(record, action=action, revised_deduction=deduction_to_write)
        if self.repo is not None:
            await self.repo.insert_review(
                qc_record_id=record.id,
                action=action,
                revised_score=record.compliance_score if action == "CORRECT" else None,
                revised_deduction=deduction_to_write,
                reason=reason,
                reviewer_id=reviewer_id,
            )
            commit = getattr(self.repo.session, "commit", None)
            if commit is not None:
                await commit()
        masked = mask_text(reason or "")
        await self.publish_done(
            qc_record_id=str(record.id),
            action=action,
            revised_deduction=deduction_to_write if action in ("CORRECT", "DISMISS") else None,
            revised_score=record.compliance_score if action == "CORRECT" else None,
            evidence_masked=masked,
        )
        return {
            "qc_record_id": str(record.id),
            "action": action,
            "qc_status": record.qc_status,
            "deduction_total": record.deduction_total,
            "compliance_score": record.compliance_score,
            "qc_result": record.qc_result,
            "risk": record.risk_level,
            "grade": record.grade,
        }

    @staticmethod
    def _resolve_correct_deduction(
        revised_deduction: int | None, revised_score: int | None
    ) -> int:
        """CORRECT 主输入为 revised_deduction；revised_score 灰度保留作换算回退。

        缺值或越界均抛 A2008（主口径 >=0；灰度字段仍按 0..100 校验）。
        """
        if revised_deduction is not None:
            value = int(revised_deduction)
            if value < 0:
                raise BizException(ErrorCode.A2008)
            return value
        if revised_score is not None:
            legacy = int(revised_score)
            if legacy < 0 or legacy > 100:
                raise BizException(ErrorCode.A2008)
            return 100 - legacy
        raise BizException(ErrorCode.A2008)

    @staticmethod
    def _finalize_reviewed(
        record: QcRecord, *, action: str, revised_deduction: int | None = None
    ) -> None:
        """REVIEWED 落定（D6）：判定权威统一为 deduction_total。

        DISMISS 归零为合规、CONFIRM 扣分不变、CORRECT 取 revised_deduction；
        compliance_score 恒写派生值 max(0, 100 - deduction_total)；
        grade 退役恒 NULL（D5）；risk_level 不由复核改写（risk 由 hits 决定，复核不重跑引擎）。
        REVIEWED 后 qc_result 不得停在 pending_review。
        """
        if action == "DISMISS":
            deduction = 0
        elif action == "CORRECT":
            deduction = int(revised_deduction or 0)
        else:
            deduction = int(record.deduction_total or 0)
        record.deduction_total = deduction
        record.compliance_score = max(0, 100 - deduction)
        record.grade = None
        if record.qc_status == "REVIEWED":
            record.qc_result = "compliant" if deduction == 0 else "violation"
