"""人工复核：sample_type 映射、落 qc_reviews、发布 qc.review.done。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.events.publisher import QcEventPublisher, sample_type_for
from app.models.qc import QcRecord
from app.repositories.review_repo import ReviewRepo
from app.services.scoring_service import grade_for
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
        revised_score: int | None = None,
        redline_tags: list[str] | None = None,
        evidence_masked: str | None = None,
    ) -> dict[str, Any]:
        return await self.publisher.publish_review_done(
            qc_record_id=qc_record_id,
            action=action,
            revised_score=revised_score,
            redline_tags=redline_tags,
            evidence_masked=evidence_masked,
        )

    async def submit(
        self,
        record: QcRecord | None,
        *,
        action: str,
        revised_score: int | None = None,
        reason: str | None = None,
        reviewer_id: UUID | None = None,
    ) -> dict[str, Any]:
        if record is None or record.qc_status == "REVIEWED":
            raise BizException(ErrorCode.A2007)
        if action not in ALLOWED_ACTIONS:
            raise BizException(ErrorCode.A2002)
        score_to_write: int | None = None
        if action == "CORRECT":
            if revised_score is None or int(revised_score) < 0 or int(revised_score) > 100:
                raise BizException(ErrorCode.A2008)
            score_to_write = int(revised_score)
            record.compliance_score = score_to_write
        record.qc_status = "REVIEWED"
        self._finalize_reviewed(record, action=action)
        if self.repo is not None:
            await self.repo.insert_review(
                qc_record_id=record.id,
                action=action,
                revised_score=score_to_write,
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
            revised_score=score_to_write,
            evidence_masked=masked,
        )
        return {
            "qc_record_id": str(record.id),
            "action": action,
            "qc_status": record.qc_status,
            "compliance_score": record.compliance_score,
            "qc_result": record.qc_result,
            "risk": record.risk_level,
            "grade": record.grade,
        }

    @staticmethod
    def _finalize_reviewed(record: QcRecord, *, action: str) -> None:
        """REVIEWED 后 qc_result 不得停在 pending_review；CORRECT 按新分重算 grade。"""
        score = int(record.compliance_score or 0)
        record.grade = grade_for(score)
        if action == "CORRECT":
            record.qc_result = "compliant" if score >= 100 else "violation"
            if score >= 100:
                record.risk_level = "low"
            return
        if action == "CONFIRM" and record.qc_result == "pending_review":
            record.qc_result = "compliant" if score >= 100 else "violation"
