"""演示灌数 API：POST /demo/seed、GET /demo/pending、GET /demo/{session_id}/trace。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_quality_repo, get_redis
from app.errors import success
from app.events.publisher import event_call_id
from app.repositories.demo_repo import DemoSeedRepo
from app.repositories.demo_store import DemoSessionStore, DemoTraceStore
from app.repositories.quality_repo import QualityRepo
from app.services.demo_seed_service import DemoSeedService
from app.services.demo_trace import CALL_QC_NOT_INVOKED, CALL_QC_STATUS_FIELD, build_langsmith_url
from common.db.session import get_session

router = APIRouter(prefix="/api/v1/qc/demo", tags=["qc-demo"])


async def get_demo_seed_service(
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis),
    quality_repo: QualityRepo = Depends(get_quality_repo),
) -> DemoSeedService:
    return DemoSeedService(DemoSeedRepo(session), DemoSessionStore(redis), quality_repo)


@router.post("/seed")
async def demo_seed(svc: DemoSeedService = Depends(get_demo_seed_service)):
    data = await svc.seed()
    return JSONResponse(status_code=200, content=success(data))


@router.get("/pending")
async def demo_pending(svc: DemoSeedService = Depends(get_demo_seed_service)):
    data = await svc.pending()
    return JSONResponse(status_code=200, content=success(data))


@router.get("/{session_id}/trace")
async def demo_trace(session_id: str, redis=Depends(get_redis)):
    store = DemoTraceStore(redis)
    events: list = []
    meta: dict = {}
    try:
        events = await store.list_events(session_id)
        meta = await store.get_meta(session_id)
    except Exception:
        events = []
        meta = {}
    call_id = event_call_id(meta.get("call_id"), session_id=session_id)
    run_id = meta.get("langsmith_run_id")
    langsmith_url = build_langsmith_url(str(run_id) if run_id else None)
    return JSONResponse(
        status_code=200,
        content=success(
            {
                "session_id": session_id,
                "call_id": call_id,
                "events": events,
                "langsmith_url": langsmith_url,
                "mcp_call_qc": CALL_QC_STATUS_FIELD,
                "mcp_call_qc_note": CALL_QC_NOT_INVOKED,
            }
        ),
    )
