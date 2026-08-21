"""混合检索 API。"""
import asyncio

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

from app.api.deps import (
    get_db,
    get_embed_client,
    get_es_client,
    get_milvus_client,
    get_rerank_client,
    success_response,
)
from app.models.search import SearchRequest, SearchResponse
from app.repositories.doc_repo import DocRepo
from app.services import query_cache
from app.services.log_service import LogService
from app.services.rate_limit import rate_limiter
from app.services.search_service import SearchService
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from app.vectorstore.rerank_client import RerankClient

logger = structlog.get_logger()
router = APIRouter(tags=["混合检索"])


@router.post("/search")
async def hybrid_search(
    req: SearchRequest,
    rate: None = Depends(rate_limiter("kb_search", 200)),
    db: AsyncSession = Depends(get_db),
    milvus: MilvusClient = Depends(get_milvus_client),
    es: ESClient = Depends(get_es_client),
    embed: EmbedClient = Depends(get_embed_client),
    rerank: RerankClient = Depends(get_rerank_client),
):
    # 缓存切面：开关关闭完全跳过；命中原样返回缓存的完整对外响应
    cache_key: str | None = None
    if settings.KB_CACHE_ENABLED:
        cache_key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        cached = await query_cache.get_cached(cache_key)
        if cached is not None:
            return cached

    log_service = LogService(db)
    search_svc = SearchService(
        milvus_client=milvus,
        es_client=es,
        embed_client=embed,
        rerank_client=rerank,
        log_service=log_service,
        doc_repo=DocRepo(db),
    )
    try:
        # 请求级总时限：防慢调用堆积形成请求风暴（对齐 step6/01 有限重试+降级原则）
        result = await asyncio.wait_for(
            search_svc.hybrid_search(
                query=req.query, category=req.category, top_k=req.top_k
            ),
            timeout=settings.KB_SEARCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("search_timeout", query=req.query, timeout=settings.KB_SEARCH_TIMEOUT)
        raise BizException(ErrorCode.A3005, "检索超时，请稍后重试")

    response = success_response(result)
    if cache_key is not None:
        await query_cache.set_cached(cache_key, response, req.category)
    return response
