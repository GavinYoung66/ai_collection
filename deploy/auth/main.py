"""鉴权 sidecar：对外认证入口 + 网关 auth_request 子请求。

契约：
- `/internal/authz`（内部）：供 nginx auth_request 调用，验证逻辑 100% 复用
  `common.security.authn.authenticate`，本文件 MUST NOT 自行解码 JWT。
- `POST /api/v1/auth/login`（对外，经网关透传）：账号密码登录，签发 JWT（access + refresh）。
- `GET  /api/v1/auth/me`（对外，经网关透传）：校验 Bearer token 并返回当前用户。
- `POST /api/v1/auth/refresh`（对外，经网关透传）：refresh_token 换 access_token。
- `POST /api/v1/audit/frontend-error`（对外，需 auth_request）：前端错误审计上报。
- `GET/POST/PUT/DELETE /api/v1/admin/users`（对外，需 auth_request + role=admin）：P1 用户 CRUD。

响应统一信封 `{code, message, data, trace_id}`（与后端各服务一致）：
- 成功：HTTP 200 + `code=0`（int，对齐 complaint-service 口径）；
- 未认证/凭证错误：HTTP 401 + `B0002`；
- 无权限：HTTP 403 + `B0003`；
- 账号锁定：HTTP 423 + `B0011`；
- 资源不存在：HTTP 404 + `B0004`；
- 资源冲突（username 重复）：HTTP 409 + `B0005`；
- 参数校验失败：HTTP 422 + `B0001`；
- 系统异常：HTTP 500 + `B0007`。

安全约束：
- 密码只做 bcrypt 校验，禁止明文比较、禁止在日志/响应中输出 password_hash；
- access_token 载荷 `sub`=user_id、`role`=规范角色值、`exp`、`type="access"`；
- refresh_token 载荷 `sub`=user_id、`exp`、`type="refresh"`（不含 role，防权限过期续期）；
- Redis key 前缀 `auth:` 与 complaint-service 的 `complaint:` 前缀隔离，避免冲突。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.config.settings import settings
from common.db.models.audit import AuditLog
from common.db.models.user import User
from common.db.session import get_session
from common.errors.codes import ErrorCode
from common.resilience._redis import get_redis
from common.security.authn import AuthResult, authenticate
from common.security.rbac import create_refresh_token, create_token, verify_token_with_reason
from common.trace.middleware import TraceMiddleware, get_trace_id
from common.utils.ip import get_client_ip

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

# === Redis key 常量 ===
_LOGIN_FAIL_PREFIX = "auth:login_fail:"
_LOGIN_FAIL_MAX = 5
_LOGIN_FAIL_TTL = 900  # 15 min
_REFRESH_PREFIX = "auth:refresh:"
_REFRESH_TTL_SEC = getattr(settings, "JWT_REFRESH_HOURS", 168) * 3600


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


def _verify_password(password: str, password_hash: str) -> bool:
    """bcrypt 校验；脏哈希视为校验失败，不冒泡成 500。"""
    try:
        return pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def _json_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# === 依赖：提取已认证用户 ===

async def _require_auth(
    authorization: str = Header(default="", alias="Authorization"),
    db: AsyncSession = Depends(get_session),
) -> User:
    """校验 access_token（type=="access"），返回 db.User；401/B0002 时抛 HTTPException。"""
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail=_envelope(ErrorCode.B0002.value, "请先登录"))
    payload, reason = verify_token_with_reason(token)
    if not payload:
        logger.info("auth reject: reason=%s", reason)
        raise HTTPException(status_code=401, detail=_envelope(ErrorCode.B0002.value, "请先登录"))
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail=_envelope(ErrorCode.B0002.value, "token 类型错误"))
    raw_id = payload.get("sub")
    try:
        user_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail=_envelope(ErrorCode.B0002.value, "请先登录"))
    user = await db.get(User, user_id)
    if user is None or user.is_delete == "1" or user.status != "active":
        raise HTTPException(status_code=401, detail=_envelope(ErrorCode.B0002.value, "请先登录"))
    return user


async def _require_admin(
    current_user: User = Depends(_require_auth),
) -> User:
    """校验 role=="admin"；403/B0003 时抛 HTTPException。"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=_envelope(ErrorCode.B0003.value, "无操作权限"))
    return current_user


# === Pydantic 模型 ===

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class FrontendErrorReport(BaseModel):
    error_type: str = Field(max_length=100)
    message: str = Field(max_length=500)
    stack_trace: str | None = Field(default=None, max_length=2000)
    url: str = Field(max_length=500)
    line: int | None = None
    col: int | None = None
    user_agent: str | None = Field(default=None, max_length=300)
    trace_id: str | None = Field(default=None, max_length=100)


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    real_name: str = Field(min_length=1, max_length=50)
    role: str = Field(pattern="^(admin|supervisor|agent|qc_reviewer|ai_trainer)$")


class AdminUserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|supervisor|agent|qc_reviewer|ai_trainer)$")
    status: str | None = Field(default=None, pattern="^(active|disabled|locked)$")
    password: str | None = Field(default=None, min_length=8, max_length=128)
    real_name: str | None = Field(default=None, min_length=1, max_length=50)


# === /internal/authz（不变） ===

@app.api_route(
    "/internal/authz",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
def authz(authorization: str = Header(default="", alias="Authorization")) -> Response:
    """nginx auth_request 子请求沿用原始方法，故全方法受理。"""
    result: AuthResult = authenticate(authorization)
    if result.ok:
        return Response(
            status_code=204,
            headers={
                "X-User-Id": result.user_id or "",
                "X-User-Role": result.role or "",
            },
        )
    logger.info("authz reject: reason=%s", result.reason)
    return Response(
        status_code=401,
        media_type="application/json",
        content=f'{{"reason":{_json_str(result.reason or "unauthorized")}}}',
    )


# === REQ-5b + REQ-5c：login（扩展 Redis 计数 + refresh_token 签发） ===

@app.post("/api/v1/auth/login")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """账号密码登录：bcrypt 校验 + Redis 失败计数 + access_token + refresh_token 签发。"""
    r = get_redis()
    fail_key = f"{_LOGIN_FAIL_PREFIX}{body.username}"
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    # ── 前置：Redis 锁定检查 ──
    current_fail = await r.get(fail_key)
    if current_fail is not None and int(current_fail) >= _LOGIN_FAIL_MAX:
        if user is not None and user.status == "locked":
            logger.info("login reject (locked): username=%s", body.username)
            return _error(423, ErrorCode.B0011.value, "账号已锁定，请15分钟后重试")
        # status 与 Redis 计数不一致（管理员解锁但 Redis key 残留）→ 仍阻止并自动锁定
        if user is not None and user.is_delete != "1":
            user.status = "locked"
            await db.commit()
        logger.info("login reject (coerced lock): username=%s", body.username)
        return _error(423, ErrorCode.B0011.value, "账号已锁定，请15分钟后重试")

    # ── 用户名不存在 / 密码错误（统一 B0002，防枚举） ──
    if user is None or user.is_delete == "1":
        # 用户名不存在也 INCR（限制攻击者探测速度），但不清零
        await r.incr(fail_key)
        await r.expire(fail_key, _LOGIN_FAIL_TTL)
        logger.info("login reject (unknown user): username=%s", body.username)
        return _error(401, ErrorCode.B0002.value, "用户名或密码错误")
    if not _verify_password(body.password, user.password_hash):
        new_count = await r.incr(fail_key)
        await r.expire(fail_key, _LOGIN_FAIL_TTL)
        if new_count >= _LOGIN_FAIL_MAX:
            user.status = "locked"
            await db.commit()
            logger.info("login reject (B0011): username=%s count=%s", body.username, new_count)
            return _error(423, ErrorCode.B0011.value, "账号已锁定，请15分钟后重试")
        logger.info("login reject (pwd wrong): username=%s count=%s", body.username, new_count)
        return _error(401, ErrorCode.B0002.value, "用户名或密码错误")

    # ── 账号停用 ──
    if user.status != "active":
        logger.info("login reject: username=%s status=%s", body.username, user.status)
        return _error(403, ErrorCode.B0003.value, "账号已停用或锁定")

    # ── 成功：清零 Redis 计数、签发双 token、写入 refresh_token 吊销标记 ──
    await r.delete(fail_key)
    access_token = create_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token(str(user.id))
    # 每个用户同时只允许一个有效 refresh_token：新 login 覆盖旧 key
    # admin 改密码 / 锁定账号时 DEL 此 key → refresh 端点 EXISTS 失败 → 吊销
    await r.setex(f"{_REFRESH_PREFIX}{user.id}", _REFRESH_TTL_SEC, "1")
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("login ok: username=%s role=%s", user.username, user.role)
    return JSONResponse(
        status_code=200,
        content=_envelope(
            0,
            "ok",
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": settings.JWT_EXPIRE_HOURS * 3600,
                "refresh_expires_in": getattr(settings, "JWT_REFRESH_HOURS", 168) * 3600,
                "user": _user_payload(user),
            },
        ),
    )


# === REQ-5c：refresh token 端点 ===

@app.post("/api/v1/auth/refresh")
async def refresh(
    authorization: str = Header(default="", alias="Authorization"),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """refresh_token 换 access_token：type=="refresh" 校验 + db.User.status 校验 + 新 access_token 签发。"""
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not token:
        return _error(422, ErrorCode.B0001.value, "参数校验失败")
    payload, reason = verify_token_with_reason(token)
    if not payload:
        logger.info("refresh reject: reason=%s", reason)
        return _error(401, ErrorCode.B0002.value, "refresh_token 无效或已过期")
    if payload.get("type") != "refresh":
        return _error(401, ErrorCode.B0002.value, "token 类型错误")
    raw_id = payload.get("sub")
    try:
        user_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError, AttributeError):
        return _error(401, ErrorCode.B0002.value, "refresh_token 无效或已过期")
    user = await db.get(User, user_id)
    if user is None or user.is_delete == "1" or user.status != "active":
        return _error(401, ErrorCode.B0002.value, "refresh_token 无效或已过期")
    # Redis 吊销标记检查：login/改密码/锁定账号 时 setex / del 此 key
    r = get_redis()
    revoke_key = f"{_REFRESH_PREFIX}{user.id}"
    existing = await r.get(revoke_key)
    if not existing:
        logger.info("refresh reject (revoked): user_id=%s", user_id)
        return _error(401, ErrorCode.B0002.value, "refresh_token 已被吊销，请重新登录")
    # refresh 后吊销当前 refresh_token（单轮使用；新 refresh_token 由下一次 login 签发）
    await r.delete(revoke_key)
    # 从 db 最新 role 重新签发 access_token（不用 refresh_token 内 role——它不含 role）
    access_token = create_token({"sub": str(user.id), "role": user.role})
    logger.info("refresh ok: username=%s", user.username)
    return JSONResponse(
        status_code=200,
        content=_envelope(
            0,
            "ok",
            {"access_token": access_token, "expires_in": settings.JWT_EXPIRE_HOURS * 3600, "token_type": "bearer"},
        ),
    )


# === 既有 me 端点（不变） ===

@app.get("/api/v1/auth/me")
async def me(
    authorization: str = Header(default="", alias="Authorization"),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """校验 Bearer token 并返回当前用户（角色以数据库为准）。"""
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not token:
        return _error(401, ErrorCode.B0002.value, "请先登录")
    payload, reason = verify_token_with_reason(token)
    if not payload:
        logger.info("me reject: reason=%s", reason)
        return _error(401, ErrorCode.B0002.value, "请先登录")
    raw_id = payload.get("sub") or payload.get("user_id")
    try:
        user_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError, AttributeError):
        return _error(401, ErrorCode.B0002.value, "请先登录")
    user = await db.get(User, user_id)
    if user is None or user.is_delete == "1":
        return _error(401, ErrorCode.B0002.value, "请先登录")
    if user.status != "active":
        return _error(403, ErrorCode.B0003.value, "账号已停用或锁定")
    return JSONResponse(status_code=200, content=_envelope(0, "ok", _user_payload(user)))


# === REQ-5a：前端错误审计端点 ===

@app.post("/api/v1/audit/frontend-error")
async def frontend_error_audit(
    body: FrontendErrorReport,
    request: Request,
    current_user: User = Depends(_require_auth),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """前端 JS 运行时错误审计上报：写入 audit_logs（action='frontend_error'）。"""
    try:
        detail = body.model_dump(exclude_none=True)
        detail["reported_at"] = datetime.now(timezone.utc).isoformat()
        ip_addr = get_client_ip(request)
        audit_log = AuditLog(
            action="frontend_error",
            user_id=current_user.id,
            trace_id=body.trace_id or get_trace_id(),
            resource_type="browser",
            detail=detail,
            ip_address=ip_addr,
        )
        db.add(audit_log)
        await db.commit()
    except Exception as exc:
        logger.error("frontend-error audit write failed: %s", exc, exc_info=True)
        return _error(500, ErrorCode.B0007.value, "系统内部错误")
    return JSONResponse(status_code=200, content=_envelope(0, "ok", {"logged": True}))


# === REQ-4：P1 Admin 用户 CRUD ===

@app.get("/api/v1/admin/users")
async def admin_list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    role: str | None = Query(default=None),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    _: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """admin 用户列表：分页 + role/status/keyword 过滤。"""
    stmt = select(User).where(User.is_delete != "1")
    if role:
        stmt = stmt.where(User.role == role)
    if status:
        stmt = stmt.where(User.status == status)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where((User.username.like(like)) | (User.real_name.like(like)))
    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    rows = (await db.execute(stmt.order_by(User.create_time.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    items = [_user_payload(u) for u in rows]
    return JSONResponse(status_code=200, content=_envelope(0, "ok", {"items": items, "total": total, "page": page, "page_size": page_size}))


@app.post("/api/v1/admin/users")
async def admin_create_user(
    body: AdminUserCreate,
    current_admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """admin 创建新用户：username 唯一校验 + bcrypt cost=12。"""
    existing = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if existing and existing.is_delete != "1":
        return _error(409, ErrorCode.B0005.value, "用户名已存在")
    hashed = pwd_context.hash(body.password)
    user = User(
        username=body.username,
        password_hash=hashed,
        real_name=body.real_name,
        role=body.role,
        status="active",
    )
    if existing and existing.is_delete == "1":
        # 原用户软删除 → 复活
        existing.is_delete = "0"
        existing.password_hash = hashed
        existing.real_name = body.real_name
        existing.role = body.role
        existing.status = "active"
        await db.commit()
        user = existing
    else:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    logger.info("admin create user ok: username=%s by=%s", body.username, current_admin.username)
    return JSONResponse(status_code=200, content=_envelope(0, "ok", _user_payload(user)))


@app.put("/api/v1/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    body: AdminUserUpdate,
    current_admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """admin 更新用户：role/status/password 重置（禁止自改 role）。"""
    try:
        target_id = uuid.UUID(user_id)
    except ValueError:
        return _error(404, ErrorCode.B0004.value, "用户不存在")
    user = await db.get(User, target_id)
    if user is None or user.is_delete == "1":
        return _error(404, ErrorCode.B0004.value, "用户不存在")
    # 禁止自杀 + 禁止自改 role（防 admin 把自己降权）
    if target_id == current_admin.id:
        if body.role is not None and body.role != "admin":
            return _error(403, ErrorCode.B0003.value, "不能修改自己的角色")
    if body.password:
        user.password_hash = pwd_context.hash(body.password)
        # admin 重置密码 → 吊销所有 refresh_token（下次 login 会重新 setex）
        _r = get_redis()
        await _r.delete(f"{_REFRESH_PREFIX}{user.id}")
    if body.role:
        user.role = body.role
    if body.status:
        user.status = body.status
        # admin 锁定/停用 → 吊销 refresh_token（双保险：refresh 端点同时验 status）
        if body.status != "active":
            _r2 = get_redis()
            await _r2.delete(f"{_REFRESH_PREFIX}{user.id}")
    if body.real_name:
        user.real_name = body.real_name
    await db.commit()
    logger.info("admin update user ok: target=%s by=%s", user.username, current_admin.username)
    return JSONResponse(status_code=200, content=_envelope(0, "ok", _user_payload(user)))


@app.delete("/api/v1/admin/users/{user_id}")
async def admin_delete_user(
    user_id: str,
    current_admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """admin 软删除用户：置 is_delete='1'（禁止自杀）。"""
    try:
        target_id = uuid.UUID(user_id)
    except ValueError:
        return _error(404, ErrorCode.B0004.value, "用户不存在")
    if target_id == current_admin.id:
        return _error(403, ErrorCode.B0003.value, "不能删除自己")
    user = await db.get(User, target_id)
    if user is None or user.is_delete == "1":
        return _error(404, ErrorCode.B0004.value, "用户不存在")
    user.is_delete = "1"
    await db.commit()
    logger.info("admin delete user ok: target=%s by=%s", user.username, current_admin.username)
    return JSONResponse(status_code=200, content=_envelope(0, "ok", {"deleted": True}))


# === 异常处理器（不变） ===

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """入参校验失败：与各服务一致，返回 422 + B0001 信封（不含字段级明细）。"""
    return _error(422, ErrorCode.B0001.value, "参数校验失败")


@app.exception_handler(Exception)
async def uncaught_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("auth sidecar uncaught: %s", exc, exc_info=True)
    return _error(500, ErrorCode.B0007.value, "系统内部错误")


# FastAPI HTTPException 兜底（_require_auth / _require_admin 抛出）

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """_require_auth/_require_admin 抛出的 HTTPException 含 detail=envelope dict 时直接返回。"""
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return _error(exc.status_code, ErrorCode.B0007.value, str(detail) or "系统内部错误")
