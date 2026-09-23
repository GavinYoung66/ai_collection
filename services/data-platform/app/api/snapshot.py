"""dw.ads_case_loop 快照接口（data-platform/min-loop）。

- GET  /api/v1/data/snapshot/{case_id}：只读，返回快照行 14 列，供桥反查 session_id。
- PUT  /api/v1/data/snapshot/{case_id}：内部回写口，body 只含
  complaint_id/verdict/task_status，只更新该 case_id 已有行，不新增行。

两个接口均不加入 api-gateway 对外路由（见 deploy/gateway/nginx.conf），
不新做登录；响应沿用统一信封 {code,message,data,trace_id}，不新增错误码段。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.snapshot import get_snapshot, update_complaint_cols
from common.db.session import get_session
from common.errors.codes import ERROR_MESSAGES
from common.trace.middleware import get_trace_id

router = APIRouter(prefix="/api/v1/data", tags=["snapshot"])


class SnapshotBackflowRequest(BaseModel):
    """内部回写请求体：恰好三个投诉列，多传字段直接 B0001 拒绝。"""

    model_config = ConfigDict(extra="forbid")

    complaint_id: str = Field(min_length=1, max_length=36)
    verdict: str = Field(min_length=1, max_length=30)
    task_status: str = Field(min_length=1, max_length=20)


def _envelope(*, code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "trace_id": get_trace_id()}


def _ok(data: Any) -> JSONResponse:
    return JSONResponse(status_code=200, content=_envelope(code="0", message="ok", data=data))


def _not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=_envelope(code="B0004", message=ERROR_MESSAGES["B0004"], data=None),
    )


@router.get("/snapshot/{case_id}")
async def get_case_snapshot(
    case_id: str,
    session: AsyncSession = Depends(get_session),
):
    data = await get_snapshot(session, case_id)
    if data is None:
        return _not_found()
    return _ok(data)


@router.put("/snapshot/{case_id}")
async def put_case_snapshot(
    case_id: str,
    body: SnapshotBackflowRequest,
    session: AsyncSession = Depends(get_session),
):
    updated = await update_complaint_cols(
        session,
        case_id,
        complaint_id=body.complaint_id,
        verdict=body.verdict,
        task_status=body.task_status,
    )
    if not updated:
        # 快照行不存在：不补插行（回写只允许覆盖种子已建的那一行）。
        return _not_found()
    await session.commit()
    data = await get_snapshot(session, case_id)
    return _ok(data)
