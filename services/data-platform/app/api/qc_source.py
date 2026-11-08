"""M1 只读拉数：qc-source / pending-qc。禁止 POST 回调 qc。

T91 隔离声明：本路由组不加入 api-gateway 对外路由（deploy/gateway/nginx.conf），
且 MUST 携带服务间凭证（内部 JWT）方可访问——"网关未挂载"不作为唯一防护表述；
与 snapshot.py 既有声明口径对齐（spec 5.5.1 规则 2"代码内显式声明可查证"）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_service_credential
from app.services.qc_source import get_qc_source, list_pending_session_ids
from common.db.session import get_session
from common.errors.codes import ERROR_MESSAGES
from common.trace.middleware import get_trace_id

router = APIRouter(
    prefix="/api/v1/data",
    tags=["qc-source"],
    dependencies=[Depends(require_service_credential)],
)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _envelope(*, code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "trace_id": get_trace_id()}


def _ok(data: Any) -> JSONResponse:
    return JSONResponse(status_code=200, content=_envelope(code="0", message="ok", data=data))


def _today_shanghai() -> date:
    return datetime.now(_SHANGHAI).date()


@router.get("/sessions:pending-qc")
async def pending_qc(
    etl_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    day = etl_date or _today_shanghai()
    ids = await list_pending_session_ids(session, etl_date=day, limit=limit)
    return _ok({"etl_date": day.isoformat(), "session_ids": ids})


@router.get("/sessions/{session_id}/qc-source")
async def qc_source(
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    data = await get_qc_source(session, session_id)
    if data is None:
        return JSONResponse(
            status_code=404,
            content=_envelope(code="B0004", message=ERROR_MESSAGES["B0004"], data=None),
        )
    return _ok(data)
