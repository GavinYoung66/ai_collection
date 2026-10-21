"""统一鉴权底座：JWT + API Key 双方案，供网关 sidecar 与各服务复用。

设计要点（对应 change api-gateway-hardening D5 / specs/security/authn）：
- JWT 验签/过期/iss/aud 全部复用并扩展自 common.security.rbac，
  本文件不得自行引入 jose 库或重复实现 JWT 解码（单一验签实现）。
- role 直接取自 JWT payload 的 role 字段，与 require_role.py 语义一致，
  不引入第二套角色模型。
- API Key 使用 hmac.compare_digest 恒定时间比对，防时序攻击。
- 密钥与配置全部经 settings 读取，不硬编码。
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from common.config.settings import settings
from common.security.rbac import verify_token_with_reason

_BEARER_PREFIX = "Bearer "


@dataclass
class AuthResult:
    """统一鉴权结果（供 task 4 sidecar 复用，勿用裸 tuple/dict）。"""

    ok: bool
    user_id: str | None = None
    role: str | None = None
    reason: str | None = None


def authenticate_jwt(token: str) -> AuthResult:
    """验证 JWT，返回身份或明确拒绝原因。"""
    payload, reason = verify_token_with_reason(token)
    if reason is not None:
        return AuthResult(ok=False, reason=reason)
    # 身份字段语义与 require_role.py / qc auth.user_uuid 一致：
    # user_id 取 sub 或 user_id；role 取 payload.role（不二次建模）。
    raw_user = payload.get("sub") or payload.get("user_id")
    user_id = str(raw_user) if raw_user is not None else None
    role = payload.get("role")
    return AuthResult(ok=True, user_id=user_id, role=role)


def authenticate_api_key(key: str) -> AuthResult:
    """恒定时间比对 API Key，命中返回 role=settings.API_KEY_ROLE。"""
    raw = settings.API_KEYS or ""
    candidates = [c.strip() for c in raw.split(",") if c.strip()]
    if not candidates:
        return AuthResult(ok=False, reason="invalid api key")
    matched = False
    for candidate in candidates:
        # 恒定时间比对 + 遍历全部候选：命中不早停，
        # 避免合法/非法 Key 及 Key 在列表中位置的时序泄露。
        if hmac.compare_digest(key, candidate):
            matched = True
    if matched:
        return AuthResult(
            ok=True,
            user_id=f"api_key:{key[:8]}",
            role=settings.API_KEY_ROLE,
        )
    return AuthResult(ok=False, reason="invalid api key")


def authenticate(authorization: str) -> AuthResult:
    """统一入口：解析 Authorization 头，按形态派发 JWT 或 API Key。

    派发规则（D5）：含 '.' 且能切出三段非空 → 走 JWT；否则走 API Key。
    JWT 失败不回退 API Key（避免把伪造 JWT 当 Key 误判）。
    """
    if not authorization or not isinstance(authorization, str):
        return AuthResult(ok=False, reason="missing credentials")
    if not authorization.startswith(_BEARER_PREFIX):
        return AuthResult(ok=False, reason="missing credentials")
    token = authorization[len(_BEARER_PREFIX):].strip()
    if not token:
        return AuthResult(ok=False, reason="missing credentials")

    parts = token.split(".")
    if len(parts) == 3 and all(parts):
        return authenticate_jwt(token)
    return authenticate_api_key(token)
