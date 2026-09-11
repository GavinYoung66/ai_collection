"""qc-service 鉴权：复用 common.security.rbac.verify_token，不另写 JWT。

仅挂 alerts / reviews / rules；replay/records/health 不加 JWT。
"""
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from uuid import UUID

from common.errors.codes import ERROR_MESSAGES
from common.security.rbac import verify_token
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()

_AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "
_CODE_UNAUTHORIZED = "B0002"
_CODE_FORBIDDEN = "B0003"
_MSG_UNAUTHORIZED = ERROR_MESSAGES[_CODE_UNAUTHORIZED]
_MSG_FORBIDDEN = ERROR_MESSAGES[_CODE_FORBIDDEN]


class UnauthorizedError(Exception):
    """未认证：未携带或无效身份凭证。"""

    def __init__(self, message: str = _MSG_UNAUTHORIZED):
        self.message = message
        super().__init__(message)


class ForbiddenError(Exception):
    """无权限：身份有效但角色不在允许列表。"""

    def __init__(self, message: str = _MSG_FORBIDDEN):
        self.message = message
        super().__init__(message)


def user_uuid(payload: dict | None) -> UUID | None:
    if not payload:
        return None
    raw = payload.get("sub") or payload.get("user_id")
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


def require_roles(*roles: str) -> Callable:
    """JWT role ∈ roles（大小写不敏感）。返回 token payload。"""

    allowed = {item.upper() for item in roles}

    async def check(request: Request) -> dict:
        header = request.headers.get(_AUTH_HEADER, "")
        token = header[len(_BEARER_PREFIX) :].strip() if header.startswith(_BEARER_PREFIX) else ""
        if not token:
            raise UnauthorizedError()
        payload = verify_token(token)
        role = (payload or {}).get("role")
        if not payload or not role:
            raise UnauthorizedError()
        if str(role).upper() not in allowed:
            raise ForbiddenError()
        request.state.user = payload
        return payload

    return check


def register_auth_handlers(app: FastAPI) -> None:
    @app.exception_handler(UnauthorizedError)
    async def _unauthorized_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:
        logger.warning("unauthorized", message=exc.message, trace_id=get_trace_id())
        return JSONResponse(
            status_code=401,
            content={
                "code": _CODE_UNAUTHORIZED,
                "message": exc.message,
                "data": None,
                "trace_id": get_trace_id(),
            },
        )

    @app.exception_handler(ForbiddenError)
    async def _forbidden_handler(request: Request, exc: ForbiddenError) -> JSONResponse:
        logger.warning("forbidden", message=exc.message, trace_id=get_trace_id())
        return JSONResponse(
            status_code=403,
            content={
                "code": _CODE_FORBIDDEN,
                "message": exc.message,
                "data": None,
                "trace_id": get_trace_id(),
            },
        )
