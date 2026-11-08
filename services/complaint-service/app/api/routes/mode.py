"""模式切换路由（批次9：委托 mode_service）。

PUT /complaints/mode：委托 mode_service。T89：双签为两阶段审批——审批身份取自
已认证 JWT payload（request.state.user），请求体 signers 字段保留但忽略
（MUST NOT 作为判定依据）；第一阶段仅落审批登记并返回当前模式，第二不同身份
且参数一致时推进切换。失败回滚 A5014。禁止路由默认补齐双签。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_roles
from app.api.response import fail, success
from app.api.schemas.complaint import ModeSwitchRequest, ModeSwitchResponse
from app.service.mode_service import AuthenticatedApprover, mode_service
from common.errors.codes import ErrorCode

# T89：与 complaints.py 既有组级依赖同口径（complaint 版 require_roles 内嵌 auth_required 开关）
router = APIRouter(
    prefix="/complaints",
    tags=["mode"],
    dependencies=[Depends(require_roles("admin", "supervisor"))],
)


def set_redis_client(client) -> None:
    # T112：委托 service 层正式装配点（单一注入点：模式状态与 ApprovalLedger 同步切换）
    mode_service.set_redis_client(client)


def get_redis_client():
    return mode_service._redis  # noqa: SLF001


@router.put("/mode")
async def switch_mode(req: ModeSwitchRequest, request: Request):
    # T89：审批身份仅取自已认证身份；req.signers 保留但忽略（兼容请求结构，不作判定依据）
    approver = AuthenticatedApprover.from_state_user(getattr(request.state, "user", None))
    ok, err = mode_service.switch(req.mode, approver=approver, gray_percent=req.gray_percent)
    if not ok:
        return fail(err or ErrorCode.A5014, "模式切换失败，已回滚", request=request)
    data = ModeSwitchResponse(
        mode=mode_service.get_active(),
        gray_percent=mode_service.get_gray_percent(),
    )
    return success(data=data.model_dump(mode="json"), request=request)
