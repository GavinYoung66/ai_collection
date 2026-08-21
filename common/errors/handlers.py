"""全局异常处理器，统一响应包格式。"""
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException, SysException
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizException)
    async def biz_handler(request: Request, exc: BizException) -> JSONResponse:
        trace_id = get_trace_id()
        logger.warning("biz_exception", code=exc.code.value, message=exc.message, trace_id=trace_id)
        return JSONResponse(
            status_code=400,
            content={"code": exc.code.value, "message": exc.message, "data": None, "trace_id": trace_id},
        )

    @app.exception_handler(SysException)
    async def sys_handler(request: Request, exc: SysException) -> JSONResponse:
        trace_id = get_trace_id()
        logger.error("sys_exception", code=exc.code.value, message=exc.message, trace_id=trace_id)
        return JSONResponse(
            status_code=500,
            content={"code": exc.code.value, "message": exc.message, "data": None, "trace_id": trace_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        trace_id = get_trace_id()
        logger.warning("validation_error", errors=exc.errors(), trace_id=trace_id)
        return JSONResponse(
            status_code=422,
            content={"code": ErrorCode.B0001.value, "message": "参数校验失败", "data": None, "trace_id": trace_id},
        )

    @app.exception_handler(Exception)
    async def uncaught_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = get_trace_id()
        logger.error("uncaught_exception", error=str(exc), trace_id=trace_id, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"code": ErrorCode.B0007.value, "message": "系统内部错误", "data": None, "trace_id": trace_id},
        )
