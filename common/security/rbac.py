"""JWT 签发与校验。"""
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from common.config.settings import settings


def create_token(data: dict, expires_hours: int | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours or settings.JWT_EXPIRE_HOURS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        # verify_aud=False：token 携带 aud 时不得被 jose 内置校验误拒；
        # aud/iss 校验统一由 verify_token_with_reason 手动负责。
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except JWTError:
        return None


def verify_token_with_reason(token: str) -> tuple[dict | None, str | None]:
    """验签并区分失败原因，供 authn.py 复用。

    返回 (payload, reason)：成功时 reason 为 None；失败时 payload 为 None，
    reason ∈ {"token expired", "invalid signature", "issuer mismatch", "audience mismatch"}。

    验签/过期由 jose 统一处理，iss/aud 手动校验以便区分两种不匹配；
    JWT_ISSUER/JWT_AUDIENCE 为空字符串时跳过对应校验（零回归）。
    """
    try:
        # 关闭 jose 内置 aud 自动校验，由本函数手动区分 issuer/audience 不匹配；
        # iss 不传入 issuer 参数即不校验，同样手动处理。
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except ExpiredSignatureError:
        return None, "token expired"
    except JWTError:
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

    return payload, None
