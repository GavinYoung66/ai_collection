"""A2 HTTP 状态映射。信封形状复用 common（code/message/data/trace_id），不另造封装。"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.errors.exceptions import BizException
from common.trace.middleware import get_trace_id

HTTP_STATUS_BY_CODE: dict[str, int] = {
    ErrorCode.A2001.value: 200,
    ErrorCode.A2002.value: 422,
    ErrorCode.A2003.value: 200,
    ErrorCode.A2004.value: 400,
    ErrorCode.A2008.value: 400,
    ErrorCode.A2005.value: 404,
    ErrorCode.A2007.value: 404,
    ErrorCode.A2006.value: 409,
    ErrorCode.A2009.value: 200,
    ErrorCode.A2010.value: 200,
    ErrorCode.A2011.value: 200,
    ErrorCode.B0002.value: 401,
    ErrorCode.B0003.value: 403,
}

SUCCESS_CODE = "0"


def envelope(*, code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "trace_id": get_trace_id()}


def success(data: Any = None) -> dict[str, Any]:
    return envelope(code=SUCCESS_CODE, message="ok", data=data)


def is_business_success(body: dict[str, Any]) -> bool:
    """业务成败以 code===\"0\" 为准；HTTP 200 且 A2003 仍为失败。"""
    return body.get("code") == SUCCESS_CODE


def http_status_for(code: ErrorCode | str) -> int:
    key = code.value if isinstance(code, ErrorCode) else code
    return HTTP_STATUS_BY_CODE.get(key, 400)


def register_qc_exception_handlers(app: FastAPI) -> None:
    """覆盖 common 对 BizException（一律 400）与 RequestValidationError（B0001）的映射。"""

    @app.exception_handler(BizException)
    async def qc_biz_handler(request: Request, exc: BizException) -> JSONResponse:
        return JSONResponse(
            status_code=http_status_for(exc.code),
            content=envelope(code=exc.code.value, message=exc.message, data=None),
        )

    @app.exception_handler(RequestValidationError)
    async def qc_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=envelope(
                code=ErrorCode.A2002.value,
                message=ERROR_MESSAGES[ErrorCode.A2002.value],
                data=None,
            ),
        )
