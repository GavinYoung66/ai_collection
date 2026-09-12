"""Offline QC HTTP: replay / raw / batch。禁止 ingest 与 WS。"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import qc_settings
from app.deps import get_job_store, get_quality_repo, get_rag_client, get_redis
from app.errors import envelope, http_status_for, success
from app.events.publisher import QcEventPublisher
from app.integrations.dp_client import DpClient
from app.integrations.qc_session import QCSession, map_qc_source
from app.integrations.rag_client import RagClient
from app.repositories.demo_store import DemoTraceStore
from app.repositories.job_store import QcJobStore
from app.repositories.quality_repo import QualityRepo
from app.services.llm_reviewer import LLMReviewer
from app.services.offline_qc_service import CODE_OK, OfflineQcService
from app.services.replay_service import ReplayService, run_result_to_http
from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.errors.exceptions import BizException

router = APIRouter(prefix="/api/v1/qc", tags=["qc-check"])

ALLOWED_SPEAKERS = frozenset({"agent", "user", "ai", "human"})
RAW_RATE_LIMIT = 10
RAW_RATE_WINDOW = 1


class ReplayPathBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class RawTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    turn_id: str
    speaker: Literal["agent", "user", "ai", "human"]
    text: str
    turn_index: int
    hit_ts: str | None = None
    start_offset_ms: int | None = None


class RawQcSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    case_id: str
    call_ended: Literal[True]
    turns: list[RawTurn] = Field(min_length=1)
    call_id: str | None = None
    text_ready: bool | None = True
    agent_type: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: int | None = None
    result: str | None = None
    debtor_name: str | None = None
    case_snapshot: dict[str, Any] | None = None
    dial_history_24h: dict[str, Any] | None = None
    trace_id: str | None = None


class BatchBody(BaseModel):
    batch_id: str
    session_ids: list[str] = Field(min_length=1)

    @field_validator("session_ids")
    @classmethod
    def _non_empty_ids(cls, value: list[str]) -> list[str]:
        return [str(item) for item in value]


def get_dp_client() -> DpClient:
    return DpClient()


def get_llm_reviewer() -> LLMReviewer:
    return LLMReviewer()


async def get_replay_service(
    job_store: QcJobStore = Depends(get_job_store),
    quality_repo: QualityRepo = Depends(get_quality_repo),
    dp_client: DpClient = Depends(get_dp_client),
    llm: LLMReviewer = Depends(get_llm_reviewer),
    rag: RagClient = Depends(get_rag_client),
) -> ReplayService:
    llm.job_store = job_store
    llm.circuit.job_store = job_store
    redis = getattr(job_store, "_redis", None)
    publisher = QcEventPublisher(redis)
    llm.publisher = publisher
    trace_store = DemoTraceStore(redis) if redis is not None else None
    offline = OfflineQcService(
        job_store=job_store,
        llm=llm,
        publisher=publisher,
        rag=rag,
        trace_store=trace_store,
    )
    return ReplayService(
        offline,
        job_store=job_store,
        quality_repo=quality_repo,
        dp_client=dp_client,
        trace_store=trace_store,
    )


async def raw_rate_limit(request: Request, redis=Depends(get_redis)) -> None:
    user = request.headers.get("X-User-Id") or (request.client.host if request.client else "anon")
    key = f"qc:ratelimit:raw:{user}"
    try:
        incr = getattr(redis, "incr", None)
        if incr is None:
            raise BizException(ErrorCode.B0006)
        count = int(await incr(key))
        expire = getattr(redis, "expire", None)
        if count == 1 and expire is not None:
            await expire(key, RAW_RATE_WINDOW)
        if count > RAW_RATE_LIMIT:
            raise BizException(ErrorCode.B0006)
    except BizException:
        raise
    except Exception:
        raise BizException(ErrorCode.B0006)


def _force_rerun(header: str | None, body_force: bool) -> bool:
    return body_force or (header or "").strip().lower() == "true"


def _qc_json(code: str, data: Any = None, message: str | None = None) -> JSONResponse:
    msg = message or ERROR_MESSAGES.get(code, code)
    return JSONResponse(
        status_code=http_status_for(code),
        content=envelope(code=code, message=msg, data=data),
    )


def _result_response(result) -> JSONResponse:
    data = run_result_to_http(result)
    if result.code == CODE_OK:
        return JSONResponse(status_code=200, content=success(data))
    return _qc_json(result.code, data=data, message=result.message)


@router.post("/replay/raw")
async def replay_raw(
    body: RawQcSession,
    replay: ReplayService = Depends(get_replay_service),
    _: None = Depends(raw_rate_limit),
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
    force_header: str | None = Header(None, alias="X-QC-Force-Rerun"),
):
    qc = map_qc_source(body.model_dump())
    result = await replay.replay_raw(
        qc,
        force=_force_rerun(force_header, False),
        idempotency_key=idempotency_key,
    )
    return _result_response(result)


@router.post("/replay/{session_id}")
async def replay_by_id(
    session_id: str,
    replay: ReplayService = Depends(get_replay_service),
    body: ReplayPathBody | None = None,
    force_header: str | None = Header(None, alias="X-QC-Force-Rerun"),
):
    force = _force_rerun(force_header, bool(body and body.force))
    result = await replay.replay_by_session_id(session_id, force=force)
    return _result_response(result)


@router.post("/check/batch")
async def check_batch(
    body: BatchBody,
    replay: ReplayService = Depends(get_replay_service),
):
    if len(body.session_ids) > qc_settings.QC_BATCH_MAX:
        return _qc_json(ErrorCode.A2004.value)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for sid in body.session_ids:
        try:
            result = await replay.replay_by_session_id(sid, force=False)
        except BizException as exc:
            rejected.append({"session_id": sid, "code": exc.code.value})
            continue
        if result.code in {CODE_OK, ErrorCode.A2011.value}:
            row = run_result_to_http(result)
            accepted.append(
                {
                    "qc_record_id": row["qc_record_id"],
                    "session_id": row["session_id"],
                    "compliance_score": row["compliance_score"],
                    "risk": row["risk"],
                    "grade": row["grade"],
                    "qc_result": row["qc_result"],
                    "qc_status": row["qc_status"],
                }
            )
        else:
            rejected.append({"session_id": sid, "code": result.code})
    return JSONResponse(
        status_code=200,
        content=success({"batch_id": body.batch_id, "accepted": accepted, "rejected": rejected}),
    )
