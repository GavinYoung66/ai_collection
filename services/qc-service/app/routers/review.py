"""人工复核 HTTP。路径参数是 qc_record_id，不是 session_id。"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import require_roles, user_uuid
from app.deps import get_redis, get_review_repo
from app.errors import envelope, http_status_for, success
from app.events.publisher import QcEventPublisher
from app.repositories.review_repo import ReviewRepo
from app.services.review_service import ReviewService
from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.errors.exceptions import BizException

router = APIRouter(prefix="/api/v1/qc", tags=["qc-reviews"])

ReviewAction = Literal["CONFIRM", "DISMISS", "CORRECT"]


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: ReviewAction
    revised_score: int | None = Field(default=None)
    reason: str | None = None


def _qc_json(code: str, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=http_status_for(code),
        content=envelope(code=code, message=ERROR_MESSAGES.get(code, code), data=data),
    )


@router.get("/reviews/pending")
async def pending_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    repo: ReviewRepo = Depends(get_review_repo),
    _user: dict = Depends(require_roles("QC", "SUPV")),
):
    if page_size > 100:
        raise BizException(ErrorCode.A2002)
    rows, total = await repo.list_pending(page=page, page_size=page_size)
    return JSONResponse(
        status_code=200,
        content=success(
            {
                "items": [ReviewRepo.pending_item(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        ),
    )


@router.post("/reviews/{qc_record_id}")
async def submit_review(
    qc_record_id: str,
    body: ReviewBody,
    repo: ReviewRepo = Depends(get_review_repo),
    redis=Depends(get_redis),
    user: dict = Depends(require_roles("QC", "SUPV")),
):
    record = await repo.get_record(qc_record_id)
    svc = ReviewService(QcEventPublisher(redis), repo=repo)
    data = await svc.submit(
        record,
        action=body.action,
        revised_score=body.revised_score,
        reason=body.reason,
        reviewer_id=user_uuid(user),
    )
    return JSONResponse(status_code=200, content=success(data))
