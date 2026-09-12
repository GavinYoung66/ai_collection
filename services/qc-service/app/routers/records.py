"""GET 台账：list / stats / detail。先注册 /records/stats，再 /records/{session_id}。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.deps import get_quality_repo
from app.errors import envelope, http_status_for, success
from app.models.qc import GRADE_VALUES, QC_RESULT_VALUES, QC_STATUS_VALUES, RISK_LEVEL_VALUES, QcRecord, QcViolation
from app.repositories.quality_repo import QualityRepo
from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.errors.exceptions import BizException
from common.security.mask import mask_text

router = APIRouter(prefix="/api/v1/qc", tags=["qc-records"])

RiskQ = Literal["low", "medium", "high"]
GradeQ = Literal["EXCELLENT", "PASS", "WARNING", "SERIOUS"]
StatusQ = Literal["DONE", "PENDING_REVIEW", "REVIEWED", "PARTIAL"]
ResultQ = Literal["compliant", "violation", "pending_review"]
GroupByQ = Literal["agent", "rule_id", "day"]


def _as_uuid(value: str | None, *, field: str) -> UUID | None:
    if value is None or value == "":
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise BizException(ErrorCode.A2002) from exc


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _id_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def record_to_list_item(row: QcRecord) -> dict[str, Any]:
    return {
        "qc_record_id": str(row.id),
        "session_id": row.session_id,
        "case_id": _id_str(row.case_id),
        "agent_id": _id_str(row.agent_id),
        "compliance_score": row.compliance_score,
        "risk": row.risk_level,
        "grade": row.grade,
        "qc_result": row.qc_result,
        "qc_status": row.qc_status,
        "create_time": _iso(getattr(row, "create_time", None)),
    }


def violation_to_hit(row: QcViolation) -> dict[str, Any]:
    evidence = mask_text(row.evidence_text or "")
    return {
        "turn_id": row.turn_id,
        "rule_id": row.rule_id,
        "word_id": _id_str(row.word_id),
        "severity": row.severity,
        "redline_tag": row.redline_tag,
        "speaker": row.speaker,
        "score_eligible": str(row.speaker or "") != "user",
        "evidence_text": evidence,
        "source": row.source,
        "word": row.word,
        "kb_snippet": row.kb_snippet,
    }


def _qc_json(code: str, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=http_status_for(code),
        content=envelope(code=code, message=ERROR_MESSAGES.get(code, code), data=data),
    )


@router.get("/records/stats")
async def records_stats(
    group_by: GroupByQ = Query(...),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    repo: QualityRepo = Depends(get_quality_repo),
):
    if group_by not in {"agent", "rule_id", "day"}:
        raise BizException(ErrorCode.A2002)
    items = await repo.stats(group_by=group_by, from_ts=from_ts, to_ts=to_ts)
    return JSONResponse(
        status_code=200,
        content=success(
            {
                "from": _iso(from_ts),
                "to": _iso(to_ts),
                "group_by": group_by,
                "items": items,
            }
        ),
    )


@router.get("/records")
async def records_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent_id: str | None = None,
    risk: RiskQ | None = None,
    grade: GradeQ | None = None,
    qc_status: StatusQ | None = None,
    qc_result: ResultQ | None = None,
    case_id: str | None = None,
    session_id: str | None = None,
    create_time_from: datetime | None = None,
    create_time_to: datetime | None = None,
    repo: QualityRepo = Depends(get_quality_repo),
):
    if page_size > 100:
        raise BizException(ErrorCode.A2002)
    if risk is not None and risk not in RISK_LEVEL_VALUES:
        raise BizException(ErrorCode.A2002)
    if grade is not None and grade not in GRADE_VALUES:
        raise BizException(ErrorCode.A2002)
    if qc_status is not None and qc_status not in QC_STATUS_VALUES:
        raise BizException(ErrorCode.A2002)
    if qc_result is not None and qc_result not in QC_RESULT_VALUES:
        raise BizException(ErrorCode.A2002)
    rows, total = await repo.list_records(
        page=page,
        page_size=page_size,
        agent_id=_as_uuid(agent_id, field="agent_id"),
        risk=risk,
        grade=grade,
        qc_status=qc_status,
        qc_result=qc_result,
        case_id=_as_uuid(case_id, field="case_id"),
        session_id=session_id,
        create_time_from=create_time_from,
        create_time_to=create_time_to,
    )
    return JSONResponse(
        status_code=200,
        content=success(
            {
                "items": [record_to_list_item(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        ),
    )


@router.get("/records/{session_id}")
async def records_detail(
    session_id: str,
    repo: QualityRepo = Depends(get_quality_repo),
):
    row = await repo.get_by_session_id(session_id)
    if row is None:
        return _qc_json(ErrorCode.A2005.value)
    hits_src = await repo.list_violations(row.id)
    skipped = await repo.load_skipped_rules(session_id=session_id, qc_record_id=row.id)
    data = record_to_list_item(row)
    data.pop("create_time", None)
    data.update(
        {
            "high_count": row.high_count,
            "med_count": row.med_count,
            "low_count": row.low_count,
            "hits": [violation_to_hit(hit) for hit in hits_src],
            "skipped_rules": list(skipped),
        }
    )
    return JSONResponse(status_code=200, content=success(data))
