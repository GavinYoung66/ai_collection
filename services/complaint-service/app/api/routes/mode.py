"""模式切换路由（批次9：委托 mode_service）。

PUT /complaints/mode：委托 mode_service，双签校验（运营+技术负责人）。
失败回滚 A5014。禁止路由默认补齐双签。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.response import fail, success
from app.api.schemas.complaint import ModeSwitchRequest, ModeSwitchResponse
from app.service.mode_service import mode_service
from common.errors.codes import ErrorCode

router = APIRouter(prefix="/complaints", tags=["mode"])


def set_redis_client(client) -> None:
    mode_service._redis = client  # noqa: SLF001


def get_redis_client():
    return mode_service._redis  # noqa: SLF001


@router.put("/mode")
async def switch_mode(req: ModeSwitchRequest, request: Request):
    ok, err = mode_service.switch(
        req.mode, signers=list(req.signers), gray_percent=req.gray_percent,
    )
    if not ok:
        return fail(err or ErrorCode.A5014, "模式切换失败，已回滚", request=request)
    data = ModeSwitchResponse(
        mode=mode_service.get_active(),
        gray_percent=mode_service.get_gray_percent(),
    )
    return success(data=data.model_dump(mode="json"), request=request)
