"""摘要两端写入编排：ES（检索副本）→ PG（权威源）顺序，失败降级契约。

V4 摘要元数据能力（design.md D3）：
- 写入顺序固定为 ES → PG：PG 的 summary 是"一致性收口标记"——
  ES 检索副本写成功才落 PG 权威源，从根上杜绝"PG 有摘要、索引无摘要"的脏状态。
- 降级契约：
  - 补全接口（raise_on_failure=True）：ES 写失败按 retries 次退避重试（1s/2s/4s），
    重试仍失败 → 抛 A3003（复用"写入索引失败"语义），PG 不写，无半状态；
  - 上传入库（raise_on_failure=False）：ES 写失败仅 warning（含 trace_id），
    PG summary 不写（保持 NULL），正文已 active 不回滚、不返回错误。
- 全部成功：失效该文档所属 category 的查询缓存（复用既有失效机制）。
"""
import asyncio
import uuid

import structlog

from app.repositories.doc_repo import DocRepo
from app.services import query_cache
from app.vectorstore.es_client import ESClient
from app.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()


class SummaryService:
    """摘要两端写入编排服务（ES → PG），供上传入库与补全接口复用。"""

    def __init__(self, doc_repo: DocRepo, es_client: ESClient):
        self.doc_repo = doc_repo
        self.es = es_client

    async def write_summary(
        self,
        doc_id: uuid.UUID,
        summary: str,
        *,
        retries: int = 0,
        raise_on_failure: bool = False,
    ) -> bool:
        """按 ES → PG 顺序写入文档级摘要。

        参数：
            retries：ES 写失败后的重试次数（退避 1s/2s/4s…，上限 4s），补全接口传 3；
            raise_on_failure：最终失败时是否抛 A3003。True=补全接口（返回失败）；
                False=上传入库（仅 warning + 返回 False，正文不阻断）。

        返回：
            True=两端写入成功（PG 已收口、缓存已失效）；
            False=ES 写最终失败且 raise_on_failure=False（PG 未写，保持 NULL）。
        """
        trace_id = get_trace_id()
        try:
            await self._write_es_with_retry(doc_id, summary, retries)
        except Exception as e:
            if raise_on_failure:
                logger.error(
                    "summary_es_write_failed",
                    doc_id=str(doc_id),
                    error=str(e),
                    trace_id=trace_id,
                )
                raise BizException(ErrorCode.A3003, f"摘要索引写入失败: {e}")
            logger.warning(
                "summary_es_write_failed_skipped",
                doc_id=str(doc_id),
                error=str(e),
                trace_id=trace_id,
            )
            return False

        # PG 收口（一致性收口标记：ES 写成功才落 PG 权威源）
        await self.doc_repo.update_summary(doc_id, summary)

        # 全部成功：失效该文档 category 的查询缓存（复用既有失效机制）
        try:
            doc = await self.doc_repo.get_by_id(doc_id)
            if doc is not None and settings.KB_CACHE_ENABLED:
                await query_cache.invalidate_category(doc.category)
        except Exception as e:
            logger.warning(
                "summary_cache_invalidate_failed",
                doc_id=str(doc_id),
                error=str(e),
                trace_id=trace_id,
            )
        return True

    async def _write_es_with_retry(
        self, doc_id: uuid.UUID, summary: str, retries: int
    ) -> None:
        """ES doc_summary 写入（幂等，按 doc_id 重写），失败按 1s/2s/4s 退避重试。"""
        delay = 1.0
        attempt = 0
        while True:
            try:
                await self.es.update_summary(str(doc_id), summary)
                return
            except Exception as e:
                attempt += 1
                if attempt > retries:
                    raise
                logger.warning(
                    "summary_es_write_retry",
                    doc_id=str(doc_id),
                    attempt=attempt,
                    delay=delay,
                    error=str(e),
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 4.0)
