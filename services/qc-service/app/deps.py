"""FastAPI 依赖：DB session、Redis DB 3、QC settings、active qc_rules 快照。"""
from __future__ import annotations

from typing import Any, TypedDict

from redis.asyncio import Redis

from app.core.config import QcSettings, qc_settings
from common.config.settings import settings as platform_settings
from common.db.session import get_session  # noqa: F401  — Depends 复用，不重写

_redis_client: Redis | None = None


class QcRulesSnapshot(TypedDict):
    version: str
    status: str
    rules: list[Any]


def get_settings() -> QcSettings:
    return qc_settings


def build_redis() -> Redis:
    kwargs: dict[str, Any] = {
        "host": platform_settings.REDIS_HOST,
        "port": platform_settings.REDIS_PORT,
        "db": qc_settings.QC_REDIS_DB,
        "decode_responses": True,
    }
    if platform_settings.REDIS_PASSWORD:
        kwargs["password"] = platform_settings.REDIS_PASSWORD
    return Redis(**kwargs)


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = build_redis()
    return _redis_client


def reset_redis() -> None:
    global _redis_client
    _redis_client = None


def get_active_qc_rules() -> QcRulesSnapshot:
    """当前 active 规则快照注入点。0.1 无 ORM：version 来自配置，rules 空列表。"""
    return {
        "version": qc_settings.QC_DEFAULT_ACTIVE_RULES_VERSION,
        "status": "active",
        "rules": [],
    }
