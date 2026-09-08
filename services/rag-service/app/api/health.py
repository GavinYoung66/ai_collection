"""健康检查路由：依赖探活（PG/Milvus/Redis/embedding-service）。

对齐全局契约 step6/02 §5.1：
- 响应 `{status, service, version, checks, ts}`；status ∈ healthy / degraded / unhealthy；
- rag-service 依赖矩阵（§5.2）= PostgreSQL, Milvus, Redis, embedding-service；
- 默认（KB_HEALTH_DEGRADED_MODE=false）：任一核心依赖探活失败 → unhealthy（HTTP 200，不抛 500）；
- 降级模式（=true）：任一依赖失败只记录聚合告警，status 返回 degraded（HTTP 仍 200，
  不触发 docker-compose healthcheck / 容器重启），checks 保留真实状态；
- 每项探测 2s 超时，探测异常仅标记 fail，不向上抛。
"""
import asyncio
import time

import httpx
import structlog
from fastapi import APIRouter
from sqlalchemy import text

from app.settings import settings
from common.db.session import async_session_factory
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()
router = APIRouter(tags=["health"])

PROBE_TIMEOUT = 2.0  # 单项探测超时（秒）
SERVICE_NAME = "rag-service"
SERVICE_VERSION = "1.0.0"


async def _check_database() -> str:
    try:
        async with async_session_factory() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")), timeout=PROBE_TIMEOUT
            )
        return "ok"
    except Exception as e:
        logger.warning("health_db_fail", error=str(e))
        return "fail"


async def _check_redis() -> str:
    try:
        from app.api.deps import get_redis

        redis = await get_redis()
        await asyncio.wait_for(redis.ping(), timeout=PROBE_TIMEOUT)
        return "ok"
    except Exception as e:
        logger.warning("health_redis_fail", error=str(e))
        return "fail"


async def _check_milvus() -> str:
    try:
        from app.api.deps import get_milvus_client

        milvus = get_milvus_client()
        await asyncio.wait_for(milvus.ensure_collection(), timeout=PROBE_TIMEOUT)
        return "ok"
    except Exception as e:
        logger.warning("health_milvus_fail", error=str(e))
        return "fail"


async def _check_embedding() -> str:
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(f"{settings.EMBEDDING_SERVICE_URL}/health")
        if resp.status_code == 200:
            return "ok"
        logger.warning("health_embedding_http_fail", status=resp.status_code)
        return "fail"
    except Exception as e:
        logger.warning("health_embedding_fail", error=str(e))
        return "fail"


async def probe_health() -> dict:
    """并发探测四项依赖，汇总为统一 /health 响应体。

    - 默认（KB_HEALTH_DEGRADED_MODE=false）：任一依赖失败 → unhealthy（现状，容器会重启）；
    - 降级模式（=true）：任一依赖失败只记录聚合告警（含 trace_id），status 返回 degraded，
      checks 保留真实状态，HTTP 仍 200 → 不触发 docker-compose healthcheck 重启。
    """
    results = await asyncio.gather(
        _check_database(),
        _check_milvus(),
        _check_redis(),
        _check_embedding(),
    )
    checks = {
        "database": results[0],
        "milvus": results[1],
        "redis": results[2],
        "embedding": results[3],
    }
    failed = [name for name, value in checks.items() if value != "ok"]
    if not failed:
        status = "healthy"
    elif settings.KB_HEALTH_DEGRADED_MODE:
        logger.warning(
            "health_degraded_mode_dependency_down",
            failed=failed,
            trace_id=get_trace_id(),
        )
        status = "degraded"
    else:
        logger.warning(
            "health_dependency_down",
            failed=failed,
            trace_id=get_trace_id(),
        )
        status = "unhealthy"
    return {
        "status": status,
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "checks": checks,
        "ts": int(time.time()),
    }


@router.get("/health")
async def health_check():
    return await probe_health()
