"""complaint-service 鉴权：复用 common.security.rbac.verify_token。

是否强制认证由 common Settings.auth_required 决定：生产恒为 true，
本地默认 false（启动打印显著告警），避免阻断本地调试。
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common.config.settings import settings
from common.errors.codes import ERROR_MESSAGES
from common.security.rbac import verify_token
from common.trace.middleware import get_trace_id

_AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "
_CODE_UNAUTHORIZED = "B0002"
_CODE_FORBIDDEN = "B0003"


class AuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class UnauthorizedError(AuthError):
    def __init__(self):
        super().__init__(401, _CODE_UNAUTHORIZED, ERROR_MESSAGES[_CODE_UNAUTHORIZED])


class ForbiddenError(AuthError):
    def __init__(self):
        super().__init__(403, _CODE_FORBIDDEN, ERROR_MESSAGES[_CODE_FORBIDDEN])


def require_roles(*roles: str) -> Callable:
    """JWT role ∈ roles（大小写不敏感）；认证关闭时放行。"""
    allowed = {str(item).strip().lower() for item in roles}

    async def check(request: Request) -> dict | None:
        if not settings.auth_required:
            # T89：认证关闭（本地调试）时注入本地审批身份，使依赖已认证身份的
            # 逻辑（模式切换两阶段审批）本地可用；生产 auth_required=true 恒强制真实 JWT。
            request.state.user = {"sub": "local-admin", "role": "admin"}
            return None
        header = request.headers.get(_AUTH_HEADER, "")
        token = header[len(_BEARER_PREFIX) :].strip() if header.startswith(_BEARER_PREFIX) else ""
        if not token:
            raise UnauthorizedError()
        payload = verify_token(token)
        role = str((payload or {}).get("role") or "").strip().lower()
        if not payload or not role:
            raise UnauthorizedError()
        if role not in allowed:
            raise ForbiddenError()
        request.state.user = payload
        return payload

    return check


def register_auth_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def _auth_handler(request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "data": None,
                "trace_id": get_trace_id(),
            },
        )
