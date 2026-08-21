"""接口权限校验组件：路由层 Depends 拦截，先校验身份与角色，再执行业务逻辑。

- 身份凭证：`Authorization: Bearer <JWT>`，复用 common.security.rbac.verify_token；
- 未携带凭证或凭证无效：HTTP 401 + 统一错误码 B0002（请先登录）；
- 凭证有效但角色不在允许列表：HTTP 403 + 统一错误码 B0003（无操作权限）；
- 角色比较大小写不敏感（seed 数据为小写，DD 契约为大写）；
- 校验失败仅影响本接口，不触碰全局异常处理器、不影响其他接口。
"""
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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


def require_roles(*roles: str) -> Callable:
    """返回 FastAPI 依赖：要求 JWT 载荷中 role ∈ roles（大小写不敏感）。"""

    allowed = {r.upper() for r in roles}

    async def check(request: Request) -> None:
        header = request.headers.get(_AUTH_HEADER, "")
        token = (
            header[len(_BEARER_PREFIX):].strip()
            if header.startswith(_BEARER_PREFIX)
            else ""
        )
        if not token:
            raise UnauthorizedError()
        payload = verify_token(token)
        role = (payload or {}).get("role")
        if not payload or not role:
            raise UnauthorizedError()
        if role.upper() not in allowed:
            raise ForbiddenError()

    return check


def register_auth_handlers(app: FastAPI) -> None:
    """注册权限校验异常处理器：401 / 403 + 项目统一响应包。"""

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
