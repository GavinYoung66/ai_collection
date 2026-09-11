"""事后主管告警 HTTP。禁止作为通话页数据源。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth import require_roles, user_uuid
from app.deps import get_alert_repo
from app.errors import envelope, http_status_for, success
from app.repositories.alert_repo import AlertRepo
from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.errors.exceptions import BizException

router = APIRouter(prefix="/api/v1/qc", tags=["qc-alerts"])


def _qc_json(code: str, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=http_status_for(code),
        content=envelope(code=code, message=ERROR_MESSAGES.get(code, code), data=data),
    )


@router.get("/alerts")
async def list_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    status: str | None = None,
    session_id: str | None = None,
    repo: AlertRepo = Depends(get_alert_repo),
    _user: dict = Depends(require_roles("SUPV", "ADMIN")),
):
    if page_size > 100:
        raise BizException(ErrorCode.A2002)
    if status is not None and status not in {"NEW", "ACKED"}:
        raise BizException(ErrorCode.A2002)
    items, total = await repo.list_alerts(
        page=page,
        page_size=page_size,
        status=status,
        session_id=session_id,
    )
    return JSONResponse(
        status_code=200,
        content=success({"items": items, "total": total, "page": page, "page_size": page_size}),
    )


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(
    alert_id: str,
    repo: AlertRepo = Depends(get_alert_repo),
    user: dict = Depends(require_roles("SUPV", "ADMIN")),
):
    row = await repo.get(alert_id)
    if row is None or row.status == "ACKED":
        return _qc_json(ErrorCode.A2006.value)
    await repo.ack(row, acked_by=user_uuid(user))
    commit = getattr(repo.session, "commit", None)
    if commit is not None:
        await commit()
    acked_at = row.acked_at.isoformat() if row.acked_at is not None else None
    acked_by = str(row.acked_by) if row.acked_by is not None else None
    return JSONResponse(
        status_code=200,
        content=success(
            {
                "alert_id": str(row.id),
                "status": row.status,
                "acked_by": acked_by,
                "acked_at": acked_at,
            }
        ),
    )
