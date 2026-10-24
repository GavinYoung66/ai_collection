"""LLM 用量看板端点（f3-usage-console 真链路）：summary / logs。

契约与前端 `modules/usage/types.ts`（DRAFT）逐字一致；两端点均只读
`llm_call_logs`。角色：admin / supervisor（与前端路由 meta.roles 对齐，
网关 auth_request 已做一级鉴权，本层再做角色判定）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_roles
from app.errors import success
from app.repositories.llm_usage_repo import LlmUsageRepo
from common.db.session import get_session
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

router = APIRouter(prefix="/api/v1/llm-usage", tags=["llm-usage"])

_DEFAULT_WINDOW = timedelta(days=7)
_ONE_DAY = timedelta(hours=24)


async def get_llm_usage_repo(session: AsyncSession = Depends(get_session)) -> LlmUsageRepo:
    return LlmUsageRepo(session)


def _as_utc(value: datetime) -> datetime:
    """SQLAlchemy tz-aware 比较所需：naive 时间按 UTC 解释。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _resolve_window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """缺省近 7 天；start >= end 视为参数错误（B0001/A2002 语义）。"""
    end_dt = _as_utc(end) if end is not None else datetime.now(timezone.utc)
    start_dt = _as_utc(start) if start is not None else end_dt - _DEFAULT_WINDOW
    if start_dt >= end_dt:
        raise BizException(ErrorCode.A2002)
    return start_dt, end_dt


def _log_item(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "trace_id": row.trace_id,
        "session_id": row.session_id,
        "provider": row.provider,
        "model": row.model,
        "token_usage": row.token_usage,
        "latency_ms": row.latency_ms,
        "confidence": row.confidence,
        "status": row.status,
        "error_msg": row.error_msg,
        "create_time": row.create_time.isoformat() if row.create_time else None,
    }


@router.get("/summary")
async def usage_summary(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    repo: LlmUsageRepo = Depends(get_llm_usage_repo),
    _: dict = Depends(require_roles("admin", "supervisor")),
):
    start_dt, end_dt = _resolve_window(start, end)
    bucket = "hour" if end_dt - start_dt <= _ONE_DAY else "day"
    data: dict[str, Any] = {
        "totals": await repo.totals(start_dt, end_dt),
        "by_model": await repo.by_model(start_dt, end_dt),
        "trend": await repo.trend(start_dt, end_dt, bucket),
    }
    by_service = await repo.by_service(start_dt, end_dt)
    if by_service:
        data["by_service"] = by_service
    # resilience 暂不返回：fallback 次数未落库，前端按缺失整块隐藏
    return JSONResponse(status_code=200, content=success(data))


@router.get("/logs")
async def usage_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    status: str | None = Query(None),
    provider: str | None = Query(None),
    trace_id: str | None = Query(None),
    repo: LlmUsageRepo = Depends(get_llm_usage_repo),
    _: dict = Depends(require_roles("admin", "supervisor")),
):
    start_dt, end_dt = _resolve_window(start, end)
    rows, total = await repo.logs(
        start_dt,
        end_dt,
        page=page,
        page_size=page_size,
        status=status or None,
        provider=provider or None,
        trace_id=trace_id or None,
    )
    return JSONResponse(
        status_code=200,
        content=success(
            {
                "items": [_log_item(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        ),
    )
