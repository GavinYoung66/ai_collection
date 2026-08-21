"""JWT 签发与校验。"""
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from common.config.settings import settings


def create_token(data: dict, expires_hours: int | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours or settings.JWT_EXPIRE_HOURS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
