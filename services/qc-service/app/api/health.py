"""健康检查：仅服务根 GET /health（不在 /api/v1 下）。信封 + 三字段，不用 A200x。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.deps import get_rag_client, get_redis
from app.errors import success
from app.integrations.rag_client import RagClient
from app.observability import evaluate_words_stale
from app.repositories.job_store import QcJobStore, is_llm_chain_open_in_process

router = APIRouter(tags=["health"])

HEALTH_REDIS_TIMEOUT = 1.0
HEALTH_RAG_TIMEOUT = 1.0


async def _probe_health(redis, rag: RagClient) -> dict[str, Any]:
    ac_version: str | None = None
    llm_degraded = is_llm_chain_open_in_process()
    rag_connected = False
    try:
        store = QcJobStore(redis)

        async def _read() -> tuple[str | None, bool]:
            version = await store.get_words_version()
            degraded = await store.is_llm_chain_degraded()
            return version, degraded

        ac_version, llm_degraded = await asyncio.wait_for(_read(), timeout=HEALTH_REDIS_TIMEOUT)
    except Exception:
        ac_version = None
        # Redis 挂/超时：保留进程内整链口径，避免把已 open 的熔断报成 false

    try:
        rag_connected = await asyncio.wait_for(
            rag.probe_dict_reachable(timeout=HEALTH_RAG_TIMEOUT),
            timeout=HEALTH_RAG_TIMEOUT + 0.5,
        )
    except Exception:
        rag_connected = False

    if not ac_version:
        loaded = getattr(rag, "ac_ref", None) or getattr(rag, "words", None)
        if loaded:
            ac_version = str(getattr(rag, "version", None) or "loaded")

    evaluate_words_stale(
        fetch_failing=False,
        qc_version=ac_version,
        rag_version=ac_version if rag_connected else None,
    )

    if llm_degraded or not rag_connected or not ac_version:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "ac_version": ac_version,
        "llm_degraded": bool(llm_degraded),
        "rag_connected": bool(rag_connected),
    }


@router.get("/health")
async def health_check(
    redis=Depends(get_redis),
    rag: RagClient = Depends(get_rag_client),
):
    data = await _probe_health(redis, rag)
    return JSONResponse(status_code=200, content=success(data))
