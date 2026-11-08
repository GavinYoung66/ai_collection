"""JWT 签发与校验。"""
from datetime import datetime, timedelta, timezone

import jwt
import structlog
from jwt import ExpiredSignatureError, InvalidTokenError

from common.config.settings import settings

logger = structlog.get_logger()


def log_auth_mode(cfg, service: str) -> None:
    """启动时输出认证模式：生产强制开启；本地关闭时打印显著告警。"""
    if getattr(cfg, "auth_required", False):
        if getattr(cfg, "is_production", False) and not getattr(cfg, "ENABLE_AUTH", False):
            logger.warning(
                "auth_forced_by_env",
                service=service,
                env=cfg.ENV,
                message="ENV=prod 强制开启认证（配置 ENABLE_AUTH=false 被忽略）",
            )
        else:
            logger.info("auth_required", service=service, env=cfg.ENV)
    else:
        logger.warning(
            "auth_disabled",
            service=service,
            env=cfg.ENV,
            message="⚠ 认证已关闭：complaints / qc records 等含 PII 接口匿名可访问，仅限本地调试；生产自动强制开启",
        )


def create_token(data: dict, expires_hours: int | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours or settings.JWT_EXPIRE_HOURS)
    to_encode["exp"] = expire
    to_encode["type"] = "access"
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(sub: str, expires_hours: int | None = None) -> str:
    """创建 refresh_token：载荷仅含 sub，不含 role（权限变更后旧 token 不续过期 role）。"""
    to_encode: dict[str, object] = {"sub": sub, "type": "refresh"}
    refresh_hours = (
        expires_hours
        if expires_hours is not None
        else int(getattr(settings, "JWT_REFRESH_HOURS", 168))
    )
    expire = datetime.now(timezone.utc) + timedelta(hours=refresh_hours)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        # verify_aud=False：token 携带 aud 时不得被 PyJWT 内置校验误拒；
        # aud/iss 校验统一由 verify_token_with_reason 手动负责。
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except InvalidTokenError:
        return None
    # T69：令牌类型隔离——无 type 的历史 token 与 refresh token 均不得作为访问凭证
    if payload.get("type") != "access":
        return None
    return payload


def verify_token_with_reason(token: str) -> tuple[dict | None, str | None]:
    """验签并区分失败原因，供 authn.py 复用。

    返回 (payload, reason)：成功时 reason 为 None；失败时 payload 为 None，
    reason ∈ {"token expired", "invalid signature", "issuer mismatch", "audience mismatch"}。

    验签/过期由 PyJWT 统一处理，iss/aud 手动校验以便区分两种不匹配；
    JWT_ISSUER/JWT_AUDIENCE 为空字符串时跳过对应校验（零回归）。
    T69：iss/aud 校验后追加令牌类型校验——type 非 "access"（含缺失的历史
    token 与 refresh token）一律拒绝，防止 refresh token 被用作访问凭证。
    """
    try:
        # 关闭 PyJWT 内置 aud 自动校验，由本函数手动区分 issuer/audience 不匹配；
        # iss 不传入 issuer 参数即不校验，同样手动处理。
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except ExpiredSignatureError:
        return None, "token expired"
    except InvalidTokenError:
        return None, "invalid signature"

    if settings.JWT_ISSUER and payload.get("iss") != settings.JWT_ISSUER:
        return None, "issuer mismatch"

    expected_aud = settings.JWT_AUDIENCE
    if expected_aud:
        actual_aud = payload.get("aud")
        matched = False
        if isinstance(actual_aud, list):
            matched = expected_aud in actual_aud
        else:
            matched = actual_aud == expected_aud
        if not matched:
            return None, "audience mismatch"

    if payload.get("type") != "access":
        return None, "invalid token type"

    return payload, None
