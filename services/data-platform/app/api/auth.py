"""data-platform 服务间凭证校验（T91）。

数据面端点（qc-source / pending-qc / snapshot）不加入 api-gateway 对外路由，
防护 MUST NOT 仅依赖"网关未挂载"的隐式假设：对内强制服务间凭证（内部 JWT，
复用 common.security.authn 统一验签；D2.1 裁决不引入 mTLS/NetworkPolicy）。
拒绝发生在任何数据读取之前；豁免仅 health / metrics。
"""
from __future__ import annotations

import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import dp_settings
from common.errors.codes import ERROR_MESSAGES
from common.security.authn import authenticate_jwt
from common.trace.middleware import get_trace_id

_AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "
_CODE_UNAUTHORIZED = "B0002"


class ServiceCredentialError(Exception):
    """服务间凭证缺失/无效：401 统一信封（trace_id 回显），不泄露端点信息。"""

    def __init__(self, message: str = ERROR_MESSAGES[_CODE_UNAUTHORIZED]):
        self.message = message
        super().__init__(message)


async def require_service_credential(request: Request) -> dict:
    """校验 `Authorization: Bearer` 内部服务凭证，返回 principal（sub/role）。

    双模式（T91 返工裁决）：
    1. 静态预共享 token：配置 ``DP_SERVICE_TOKEN`` 后先恒定时间比对该值
       （hmac.compare_digest，命中即放行，支持调用方静态非 JWT 形态）；
    2. 内部服务 JWT：委托 common.security.authn.authenticate_jwt 验签
       （单一验签实现），且 **role 必须为 "service"**——用户角色 token
       （admin/supervisor/agent 等）即使有效也拒绝，防用户凭证读取数据面 PII。
    无效/过期/缺失/角色不符一律 ServiceCredentialError → 401，任何数据读取不发生。
    """
    header = request.headers.get(_AUTH_HEADER, "")
    token = header[len(_BEARER_PREFIX):].strip() if header.startswith(_BEARER_PREFIX) else ""
    if not token:
        raise ServiceCredentialError()
    configured = (dp_settings.DP_SERVICE_TOKEN or "").strip()
    if configured and hmac.compare_digest(token, configured):
        return {"sub": "service:static", "role": "service"}
    result = authenticate_jwt(token)
    if not result.ok:
        raise ServiceCredentialError()
    if str(result.role or "").strip().lower() != "service":
        raise ServiceCredentialError()
    return {"sub": result.user_id, "role": result.role}


def register_service_auth_handlers(app: FastAPI) -> None:
    """401 统一信封形态与该服务既有 _envelope 一致（code/message/data/trace_id）。"""

    @app.exception_handler(ServiceCredentialError)
    async def _service_credential_handler(
        request: Request, exc: ServiceCredentialError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={
                "code": _CODE_UNAUTHORIZED,
                "message": exc.message,
                "data": None,
                "trace_id": get_trace_id(),
            },
        )