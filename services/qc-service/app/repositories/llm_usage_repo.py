"""LLM 用量聚合查询：只读 llm_call_logs（f3-usage-console 真链路）。

口径声明（与前端 DRAFT 契约逐字对齐）：
- calls       = count(*)
- tokens      = sum(coalesce(token_usage->>'total_tokens'::int, 0))
- avg_latency = avg(latency_ms)，全为 NULL 时返回 None（前端渲染为「—」）
- failure_rate = 非成功状态行数 / calls；成功状态见 SUCCESS_STATUSES
  （ok / qc_review_exemption / skipped 为当前实际写入值，原列注释四值中的
  success/failed/timeout/circuit_open 一并按语义归类）
- by_service  = group by svc（0017 迁移新增列；历史行 svc 为 NULL 自然落空）
- trend       = 跨度 ≤24h 按小时、否则按天分桶（date_trunc）
- resilience  暂不返回：fallback 次数未落库，前端按「缺失即整块隐藏」处理
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.models.llm_log import LLMCallLog

SUCCESS_STATUSES: tuple[str, ...] = ("ok", "qc_review_exemption", "skipped", "success")

_TOKENS_EXPR = func.coalesce(
    func.cast(LLMCallLog.token_usage["total_tokens"].astext, Integer), 0
)
_FAIL_EXPR = case((LLMCallLog.status.notin_(SUCCESS_STATUSES), 1), else_=0)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


class LlmUsageRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _window(self, start: datetime, end: datetime):
        return (LLMCallLog.create_time >= start, LLMCallLog.create_time < end)

    async def totals(self, start: datetime, end: datetime) -> dict[str, Any]:
        stmt = select(
            func.count().label("calls"),
            func.coalesce(func.sum(_TOKENS_EXPR), 0).label("tokens"),
            func.avg(LLMCallLog.latency_ms).label("avg_latency"),
            func.coalesce(func.sum(_FAIL_EXPR), 0).label("fails"),
        ).where(*self._window(start, end))
        row = (await self.session.execute(stmt)).one()
        calls = int(row.calls or 0)
        return {
            "calls": calls,
            "tokens": int(row.tokens or 0),
            "avg_latency_ms": round(float(row.avg_latency), 1)
            if row.avg_latency is not None
            else None,
            "failure_rate": round(int(row.fails or 0) / calls, 4) if calls else 0.0,
        }

    async def by_model(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        stmt = (
            select(
                LLMCallLog.model.label("model"),
                func.count().label("calls"),
                func.coalesce(func.sum(_TOKENS_EXPR), 0).label("tokens"),
            )
            .where(*self._window(start, end), LLMCallLog.model.isnot(None))
            .group_by(LLMCallLog.model)
            .order_by(func.count().desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {"model": row.model, "calls": int(row.calls), "tokens": int(row.tokens or 0)}
            for row in rows
        ]

    async def by_service(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        stmt = (
            select(LLMCallLog.svc.label("service"), func.count().label("calls"))
            .where(*self._window(start, end), LLMCallLog.svc.isnot(None))
            .group_by(LLMCallLog.svc)
            .order_by(func.count().desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [{"service": row.service, "calls": int(row.calls)} for row in rows]

    async def trend(
        self, start: datetime, end: datetime, bucket: str
    ) -> list[dict[str, Any]]:
        bucket_expr = func.date_trunc(bucket, LLMCallLog.create_time)
        stmt = (
            select(
                bucket_expr.label("bucket_start"),
                func.count().label("calls"),
                func.coalesce(func.sum(_FAIL_EXPR), 0).label("fails"),
            )
            .where(*self._window(start, end))
            .group_by(bucket_expr)
            .order_by(bucket_expr)
        )
        rows = (await self.session.execute(stmt)).all()
        points: list[dict[str, Any]] = []
        for row in rows:
            calls = int(row.calls or 0)
            points.append(
                {
                    "bucket_start": _iso(row.bucket_start),
                    "calls": calls,
                    "failure_rate": round(int(row.fails or 0) / calls, 4) if calls else 0.0,
                }
            )
        return points

    async def logs(
        self,
        start: datetime,
        end: datetime,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        provider: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[Sequence[LLMCallLog], int]:
        conditions = list(self._window(start, end))
        if status:
            conditions.append(LLMCallLog.status == status)
        if provider:
            conditions.append(LLMCallLog.provider == provider)
        if trace_id:
            conditions.append(LLMCallLog.trace_id == trace_id)

        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(LLMCallLog).where(*conditions)
                )
            ).scalar_one()
        )
        stmt = (
            select(LLMCallLog)
            .where(*conditions)
            .order_by(LLMCallLog.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total
