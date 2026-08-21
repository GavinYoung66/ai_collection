"""全链路检索日志：query→召回→分数→耗时→落库。"""
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import KBSearchLog

logger = structlog.get_logger()


class LogService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        trace_id: str,
        query: str,
        category: str | None,
        dense_hit_count: int,
        sparse_hit_count: int,
        fused_count: int,
        reranked_count: int,
        max_rerank_score: float | None,
        hit_doc_ids: list[str],
        hit_chunk_ids: list[str],
        latency_ms: int,
        degraded: bool,
        is_fallback: bool,
    ) -> None:
        try:
            entry = KBSearchLog(
                id=uuid.uuid4(),
                trace_id=trace_id,
                query=query,
                category=category,
                dense_hit_count=dense_hit_count,
                sparse_hit_count=sparse_hit_count,
                fused_count=fused_count,
                reranked_count=reranked_count,
                max_rerank_score=max_rerank_score,
                hit_doc_ids=",".join(hit_doc_ids),
                hit_chunk_ids=",".join(hit_chunk_ids),
                latency_ms=latency_ms,
                degraded=degraded,
                is_fallback=is_fallback,
            )
            self.db.add(entry)
            await self.db.commit()
        except Exception as e:
            logger.warning("search_log_write_failed", error=str(e), trace_id=trace_id)
            await self.db.rollback()
