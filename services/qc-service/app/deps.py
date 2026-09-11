"""FastAPI 依赖：DB session、Redis DB 3、QC settings、active qc_rules 快照。"""
from __future__ import annotations

from typing import Any, TypedDict

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import QcSettings, qc_settings
from app.integrations.rag_client import RagClient
from app.repositories.alert_repo import AlertRepo
from app.repositories.job_store import QcJobStore
from app.repositories.quality_repo import QualityRepo
from app.repositories.review_repo import ReviewRepo
from app.repositories.rule_repo import RuleRepo
from app.seeds.qc_rules_v1 import V1_SEED
from common.config.settings import settings as platform_settings
from common.db.session import get_session  # noqa: F401  — Depends 复用，不重写

_redis_client: Redis | None = None
_rag_client: RagClient | None = None


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
    """当前 active 规则快照。默认 v1.0 种子（DB 切换由 rule_repo 在后续轮次加载）。"""
    return {
        "version": qc_settings.QC_DEFAULT_ACTIVE_RULES_VERSION,
        "status": "active",
        "rules": list(V1_SEED["rules"]),
    }


def get_rag_client() -> RagClient:
    global _rag_client
    if _rag_client is None:
        _rag_client = RagClient()
    return _rag_client


def reset_rag_client() -> None:
    global _rag_client
    _rag_client = None


async def get_job_store(redis=Depends(get_redis)) -> QcJobStore:
    return QcJobStore(redis)


async def get_quality_repo(
    session: AsyncSession = Depends(get_session),
    job_store: QcJobStore = Depends(get_job_store),
) -> QualityRepo:
    return QualityRepo(session, job_store)


async def get_alert_repo(session: AsyncSession = Depends(get_session)) -> AlertRepo:
    return AlertRepo(session)


async def get_review_repo(session: AsyncSession = Depends(get_session)) -> ReviewRepo:
    return ReviewRepo(session)


async def get_rule_repo(session: AsyncSession = Depends(get_session)) -> RuleRepo:
    return RuleRepo(session)
