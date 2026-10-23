"""规则版本查询与原子切换。strategy.updated 只刷新缓存，本路由才切库。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import require_roles
from app.deps import get_job_store, get_rule_repo
from app.errors import envelope, http_status_for, success
from app.repositories.rule_repo import RuleRepo
from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.errors.exceptions import BizException

router = APIRouter(prefix="/api/v1/qc", tags=["qc-rules"])


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str = Field(min_length=1)


def _qc_json(code: str, data=None) -> JSONResponse:
    return JSONResponse(
        status_code=http_status_for(code),
        content=envelope(code=code, message=ERROR_MESSAGES.get(code, code), data=data),
    )


@router.get("/rules/active")
async def get_active_rules(
    repo: RuleRepo = Depends(get_rule_repo),
    _user: dict = Depends(require_roles("admin", "ai_trainer")),
):
    row = await repo.get_active()
    if row is None or not row.status == "active":
        return _qc_json(ErrorCode.A2009.value)
    return JSONResponse(status_code=200, content=success(RuleRepo.to_active_payload(row)))


@router.post("/rules/apply")
async def apply_rules(
    body: ApplyBody,
    repo: RuleRepo = Depends(get_rule_repo),
    job_store=Depends(get_job_store),
    _user: dict = Depends(require_roles("admin")),
):
    row, previous = await repo.apply_version(body.version)
    if job_store is not None:
        await job_store.set_rules_active(row.version)
    return JSONResponse(
        status_code=200,
        content=success(
            {
                "version": row.version,
                "status": row.status,
                "previous_version": previous,
            }
        ),
    )
