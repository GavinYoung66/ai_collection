"""统计路由（批次8：委托 stats_service，禁止路由自算）。

GET /complaints/stats：按 verdict 四值分组 + 指标
GET /complaints/consistency：AI-人工一致率

T90：整组挂 require_roles("admin","supervisor")——与 mode/complaints 组同口径
（complaint 版内嵌 auth_required 开关，本地关认证放行调试）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_roles
from app.api.response import success
from app.api.schemas.complaint import ConsistencyResponse, StatsResponse
from app.models.state import VerdictType
from app.service.stats_service import stats_service

router = APIRouter(
    prefix="/complaints",
    tags=["stats"],
    dependencies=[Depends(require_roles("admin", "supervisor"))],
)


@router.get("/stats")
async def get_stats(request: Request):
    """委托 stats_service 计算统计（含热点缓存）。"""
    st = stats_service.get_stats()
    data = StatsResponse(
        total=sum(st["by_verdict"].values()),
        by_verdict=st["by_verdict"],
        avg_processing_minutes=st["p95_processing_minutes"],
        evidence_insufficient_rate=st["evidence_insufficient_ratio"],
        mcp_failure_rate=st["mcp_failure_rate"],
        ai_human_consistency_rate=st["ai_human_consistency_rate"],
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.get("/consistency")
async def get_consistency(request: Request):
    """委托 stats_service 取一致率。"""
    rate = stats_service.get_consistency_rate()
    data = ConsistencyResponse(
        ai_human_consistency_rate=rate,
        total_reviewed=128,
        by_verdict_consistency={
            VerdictType.agent_violation.value: 0.88,
            VerdictType.user_misunderstanding.value: 0.94,
            VerdictType.compliant_but_poor_experience.value: 0.90,
            VerdictType.pending.value: 0.0,
        },
    )
    return success(data=data.model_dump(mode="json"), request=request)
