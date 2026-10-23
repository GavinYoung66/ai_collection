"""鉴权 sidecar：对外认证入口 + 网关 auth_request 子请求。

契约：
- `/internal/authz`（内部）：供 nginx auth_request 调用，验证逻辑 100% 复用
  `common.security.authn.authenticate`，本文件 MUST NOT 自行解码 JWT。
- `POST /api/v1/auth/login`（对外，经网关透传）：账号密码登录，签发 JWT。
- `GET  /api/v1/auth/me`（对外，经网关透传）：校验 Bearer token 并返回当前用户。

响应统一信封 `{code, message, data, trace_id}`（与后端各服务一致）：
- 成功：HTTP 200 + `code=0`（int，对齐 complaint-service 口径）；
- 未认证/凭证错误：HTTP 401 + `B0002`；
- 账号停用：HTTP 403 + `B0003`；
- 参数校验失败：HTTP 422 + `B0001`；
- 系统异常：HTTP 500 + `B0007`。

安全约束：
- 密码只做 bcrypt 校验，禁止明文比较、禁止在日志/响应中输出 password_hash；
- JWT 载荷固定 `sub`=user_id、`role`=规范角色值（user.py 的 5 种之一）、`exp`。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.config.settings import settings
from common.db.models.user import User
from common.db.session import get_session
from common.security.authn import AuthResult, authenticate
from common.security.rbac import create_token, verify_token_with_reason
from common.trace.middleware import TraceMiddleware, get_trace_id

# 独立审计日志：不依赖 uvicorn.error 等 ASGI 专属 logger，避免更换服务器
# （hypercorn/gunicorn）时日志丢失。显式 StreamHandler 写 stderr，docker logs 可见。
logger = logging.getLogger("auth_sidecar")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

# 与 scripts/seed/seed_users.py 保持同一套算法（bcrypt, cost=12）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

app = FastAPI(title="api-gateway auth sidecar")
app.add_middleware(TraceMiddleware)


def _envelope(code: int | str, message: str, data: object = None) -> dict:
    return {"code": code, "message": message, "data": data, "trace_id": get_trace_id()}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(code, message))


def _user_payload(user: User) -> dict:
    """对外用户视图：不返回 password_hash / is_delete 等内部字段。"""
    return {
        "user_id": str(user.id),
        "username": user.username,
        "real_name": user.real_name,
        "role": user.role,
        "status": user.status,
    }


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


def _verify_password(password: str, password_hash: str) -> bool:
    """bcrypt 校验；脏哈希视为校验失败，不冒泡成 500。"""
    try:
        return pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


@app.api_route(
    "/internal/authz",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
def authz(authorization: str = Header(default="", alias="Authorization")) -> Response:
    """nginx auth_request 子请求沿用原始方法，故全方法受理。"""
    result: AuthResult = authenticate(authorization)
    if result.ok:
        # 204 无 body 但可携带响应头；nginx 经 auth_request_set 捕获。
        return Response(
            status_code=204,
            headers={
                "X-User-Id": result.user_id or "",
                "X-User-Role": result.role or "",
            },
        )
    logger.info("authz reject: reason=%s", result.reason)
    # 仅调试用 reason，不含业务错误码（业务信封由 nginx @auth_required 产出）。
    return Response(
        status_code=401,
        media_type="application/json",
        content=f'{{"reason":{_json_str(result.reason or "unauthorized")}}}',
    )


@app.post("/api/v1/auth/login")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """账号密码登录：校验 bcrypt 哈希，签发 JWT。"""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if (
        user is None
        or user.is_delete == "1"
        or not _verify_password(body.password, user.password_hash)
    ):
        logger.info("login reject: username=%s", body.username)
        return _error(401, "B0002", "用户名或密码错误")
    if user.status != "active":
        logger.info("login reject: username=%s status=%s", body.username, user.status)
        return _error(403, "B0003", "账号已停用或锁定")

    token = create_token({"sub": str(user.id), "role": user.role})
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("login ok: username=%s role=%s", user.username, user.role)
    return JSONResponse(
        status_code=200,
        content=_envelope(
            0,
            "ok",
            {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": settings.JWT_EXPIRE_HOURS * 3600,
                "user": _user_payload(user),
            },
        ),
    )


@app.get("/api/v1/auth/me")
async def me(
    authorization: str = Header(default="", alias="Authorization"),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """校验 Bearer token 并返回当前用户（角色以数据库为准，防止 token 角色陈旧）。"""
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not token:
        return _error(401, "B0002", "请先登录")
    payload, reason = verify_token_with_reason(token)
    if not payload:
        logger.info("me reject: reason=%s", reason)
        return _error(401, "B0002", "请先登录")
    raw_id = payload.get("sub") or payload.get("user_id")
    try:
        user_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError, AttributeError):
        return _error(401, "B0002", "请先登录")
    user = await db.get(User, user_id)
    if user is None or user.is_delete == "1":
        return _error(401, "B0002", "请先登录")
    if user.status != "active":
        return _error(403, "B0003", "账号已停用或锁定")
    return JSONResponse(status_code=200, content=_envelope(0, "ok", _user_payload(user)))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """入参校验失败：与各服务一致，返回 422 + B0001 信封（不含字段级明细）。"""
    return _error(422, "B0001", "参数校验失败")


@app.exception_handler(Exception)
async def uncaught_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("auth sidecar uncaught: %s", exc, exc_info=True)
    return _error(500, "B0007", "系统内部错误")


def _json_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
