"""混合检索 API。"""
import asyncio
import time
import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.trace.middleware import get_trace_id

from app.api.deps import (
    get_db,
    get_embed_client,
    get_es_client,
    get_jwt_payload,
    get_llm_service,
    get_milvus_client,
    get_rerank_client,
    get_rewrite_service,
    get_routing_service,
    get_row_access_service,
    get_session_id,
    get_session_service,
    get_weighting_service,
    success_response,
)
from app.models.search import SearchRequest
from app.repositories.asset_repo import AssetRepo
from app.repositories.doc_repo import DocRepo
from app.services import query_cache
from app.services.llm_service import LLMService
from app.services.log_service import LogService
from app.services.query_rewrite_service import RewriteService
from app.services.rate_limit import rate_limiter
from app.services.routing_service import RoutingService
from app.services.row_access_service import RowAccessService
from app.services.search_service import SearchService
from app.services.session_service import SessionService
from app.services.weighting_service import WeightingService
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from app.vectorstore.rerank_client import RerankClient

logger = structlog.get_logger()
router = APIRouter(tags=["混合检索"])


@router.post("/search")
async def hybrid_search(
    req: SearchRequest,
    request: Request,
    response: Response,
    rate: None = Depends(rate_limiter("kb_search", 200)),
    db: AsyncSession = Depends(get_db),
    milvus: MilvusClient = Depends(get_milvus_client),
    es: ESClient = Depends(get_es_client),
    embed: EmbedClient = Depends(get_embed_client),
    rerank: RerankClient = Depends(get_rerank_client),
    rewrite_svc: RewriteService = Depends(get_rewrite_service),
    routing_svc: RoutingService = Depends(get_routing_service),
    weighting_svc: WeightingService = Depends(get_weighting_service),
    row_access_svc: RowAccessService = Depends(get_row_access_service),
    session_svc: SessionService = Depends(get_session_service),
    llm_svc: LLMService = Depends(get_llm_service),
):
    """混合检索（V4 新增响应字段 `results[].summary`；V5 新增 `answer`/`results[].assets`）。

    - 存量行为不变：query/category/top_k/rewrite 入参、Milvus+ES 双路召回、
      RRF 融合、Rerank、双闸门兜底、降级/缓存/日志/多轮会话全部保持 V3 行为；
    - **新增（V4）**：`results[]` 每项新增 `summary` 字段（str，可空，永远存在）——
      `KB_SUMMARY_ENABLED=true` 时取 PG 文档级摘要（缺失为 null），开关关闭时恒为 null；
    - **新增（V4）**：`KB_SUMMARY_ENABLED=true` 时 ES 路对 `doc_summary` 字段 BM25 加权
      （权重 `KB_SUMMARY_ES_BOOST`），开关关闭时 ES query 与 V3 完全一致；
    - **新增（V5）**：请求可选 `answer?: bool`（缺省 false）——`KB_LLM_ANSWER_ENABLED=true`
      且检索未触发双闸门兜底时调用 LLM 生成回答，响应新增 `answer`（可空）与
      `results[].assets`（可空数组，内嵌图片/表格预览元数据）；开关关闭时响应与 V4 完全一致；
    - **新增（V5）**：`answer=true` 请求跳过查询缓存读取且不写缓存（动态内容不进缓存），
      存量调用方（不传 answer）缓存行为与 V4 完全一致。
    """
    # V2: 从请求头提取 session_id + jwt_payload
    session_id = get_session_id(request)
    jwt_payload = get_jwt_payload(request)
    # 响应头透传 X-Session-Id（auto-gen 时客户端可拿到新 ID）
    response.headers["X-Session-Id"] = session_id

    # V5: answer=true 请求跳过查询缓存读取且不写缓存（LLM 回答为动态内容，
    # 避免缓存响应缺 answer 语义悬空；存量调用方不传 answer 缓存行为与 V4 完全一致）
    skip_cache = bool(req.answer)

    # 缓存切面：开关关闭完全跳过；命中原样返回缓存的完整对外响应
    cache_key: str | None = None
    if settings.KB_CACHE_ENABLED and not skip_cache:
        cache_key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        cache_start = time.time()
        cached = await query_cache.get_cached(cache_key)
        if cached is not None:
            # 缓存命中也必须落 kb_search_logs（cache_hit='1'），
            # 否则命中率监控失真（V1-T20，对齐 step6/02 检索命中率指标）
            await LogService(db).log(
                trace_id=get_trace_id(),
                query=req.query,
                category=req.category,
                dense_hit_count=0,
                sparse_hit_count=0,
                fused_count=0,
                reranked_count=0,
                max_rerank_score=None,
                hit_doc_ids=[],
                hit_chunk_ids=[],
                latency_ms=int((time.time() - cache_start) * 1000),
                degraded=False,
                is_fallback=False,
                cache_hit="1",
                query_original=req.query,
                query_rewritten=None,
                intent_tag=None,
                session_id=session_id,
            )
            return cached

    log_service = LogService(db)
    doc_repo = DocRepo(db)
    asset_repo = AssetRepo(db)
    search_svc = SearchService(
        milvus_client=milvus,
        es_client=es,
        embed_client=embed,
        rerank_client=rerank,
        log_service=log_service,
        doc_repo=doc_repo,
        # V2 服务（开关关闭时 SearchService 内部不调用）
        rewrite_service=rewrite_svc,
        routing_service=routing_svc,
        weighting_service=weighting_svc,
        row_access_service=row_access_svc,
        session_service=session_svc,
        # V5 服务（开关关闭时 SearchService 内部不调用）
        asset_repo=asset_repo,
        llm_service=llm_svc,
    )
    # LLM 生成需额外耗时：answer=true 时放宽总时限，避免 15s 截断在 LLM 调用前
    search_timeout = settings.KB_SEARCH_TIMEOUT
    if req.answer and settings.KB_LLM_ANSWER_ENABLED:
        search_timeout = max(search_timeout, float(settings.LLM_TIMEOUT) + 10.0)

    try:
        # 请求级总时限：防慢调用堆积形成请求风暴（对齐 step6/01 有限重试+降级原则）
        result = await asyncio.wait_for(
            search_svc.hybrid_search(
                query=req.query,
                category=req.category,
                top_k=req.top_k,
                session_id=session_id,
                rewrite=req.rewrite,
                jwt_payload=jwt_payload,
                answer=req.answer,
            ),
            timeout=search_timeout,
        )
    except asyncio.TimeoutError:
        logger.error("search_timeout", query=req.query, timeout=settings.KB_SEARCH_TIMEOUT)
        raise BizException(ErrorCode.A3005, "检索超时，请稍后重试")

    # V4: 响应组装填充各结果项 summary（字段永远存在）。
    # 开关开启时取 PG 文档级摘要（缺失为 null）；关闭时恒为 null，不改变既有检索逻辑。
    results = result.get("results") or []
    if results:
        if settings.KB_SUMMARY_ENABLED:
            doc_ids = [uuid.UUID(d) for d in {r["doc_id"] for r in results if r.get("doc_id")}]
            summaries = await doc_repo.get_summaries_map(doc_ids) if doc_ids else {}
            for item in results:
                item["summary"] = summaries.get(item.get("doc_id", ""))
        else:
            for item in results:
                item["summary"] = None

    resp = success_response(result)
    # 兜底/降级响应不写缓存（V1-T19），避免故障/无匹配结果被缓存 1 小时
    if cache_key is not None and query_cache.is_cacheable(result):
        await query_cache.set_cached(cache_key, resp, req.category)
    return resp
