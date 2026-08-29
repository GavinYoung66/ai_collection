"""混合检索编排：向量召回 + BM25 召回 → RRF 融合 → Rerank → 兜底。

V2 增量（开关门控，默认全 false = V1 行为）：
- Query 改写（KB_QUERY_REWRITE_ENABLED）
- 动态路由权重（KB_ROUTING_ENABLED）
- 同 doc_id 去重（强制无开关，始终生效）
- 业务加权（KB_WEIGHTING_ENABLED）
- 行级权限（KB_ROW_LEVEL_ENABLED）
- 多轮会话（KB_MULTITURN_ENABLED）
"""
import asyncio
import re
import time
import unicodedata
import uuid

import structlog

from app.repositories.asset_repo import AssetRepo
from app.repositories.doc_repo import DocRepo
from app.services.llm_service import LLMService
from app.services.log_service import LogService
from app.services.query_rewrite_service import RewriteService
from app.services.routing_service import RoutingService
from app.services.row_access_service import RowAccessService
from app.services.search_trace import SearchTraceCollector
from app.services.session_service import SessionService
from app.services.weighting_service import WeightingService
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from app.vectorstore.rerank_client import RerankClient
from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()

_FALLBACK_MESSAGE = (
    "知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。"
)

# 业务实体 ID（如 CS20260020）——结构化查询精确加权
_ENTITY_ID_RE = re.compile(r"CS\d{6,}", re.IGNORECASE)


def _extract_entity_ids(query: str) -> list[str]:
    return list(dict.fromkeys(_ENTITY_ID_RE.findall(query or "")))


def _apply_entity_boost(items: list[dict], entity_ids: list[str]) -> list[dict]:
    """含实体 ID 的查询：命中 ID 的 chunk 强制置顶。"""
    if not entity_ids or not items:
        return items
    boosted = []
    for item in items:
        content = item.get("content") or ""
        if any(eid in content for eid in entity_ids):
            item = {**item, "entity_match": True}
            item["rerank_score"] = max(float(item.get("rerank_score", 0.0)), 0.95)
        else:
            item = {**item, "entity_match": False}
        boosted.append(item)
    return sorted(
        boosted,
        key=lambda x: (
            x.get("entity_match", False),
            x.get("rerank_score", 0.0) * x.get("weight", 1.0),
        ),
        reverse=True,
    )


def _safe_uuid(value: str) -> uuid.UUID | None:
    """解析 UUID；非法值返回 None（召回脏数据跳过，不中断检索，rag-service-final-fix 5.13）。"""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


class SearchService:
    def __init__(
        self,
        milvus_client: MilvusClient,
        es_client: ESClient,
        embed_client: EmbedClient,
        rerank_client: RerankClient,
        log_service: LogService,
        doc_repo: DocRepo | None = None,
        # V2 可选依赖（开关关闭时可为 None）
        rewrite_service: RewriteService | None = None,
        routing_service: RoutingService | None = None,
        weighting_service: WeightingService | None = None,
        row_access_service: RowAccessService | None = None,
        session_service: SessionService | None = None,
        # V5 可选依赖（开关关闭时可为 None，零开销）
        asset_repo: AssetRepo | None = None,
        llm_service: LLMService | None = None,
    ):
        self.milvus = milvus_client
        self.es = es_client
        self.embed = embed_client
        self.rerank = rerank_client
        self.logger = log_service
        self.doc_repo = doc_repo
        # V2 服务
        self._rewrite_svc = rewrite_service
        self._routing_svc = routing_service
        self._weighting_svc = weighting_service
        self._row_access_svc = row_access_service
        self._session_svc = session_service
        # V5 服务
        self._asset_repo = asset_repo
        self._llm_svc = llm_service

    async def hybrid_search(
        self,
        query: str,
        category: str | None = None,
        top_k: int = 5,
        # V2 参数
        session_id: str | None = None,
        rewrite: bool = False,
        jwt_payload: dict | None = None,
        # V5 参数
        answer: bool = False,
    ) -> dict:
        start = time.time()
        trace_id = get_trace_id()
        degraded = False
        trace = SearchTraceCollector(trace_id=trace_id, query=query)
        entity_ids = _extract_entity_ids(query)

        # V2 日志辅助字段
        query_original = query
        query_rewritten: str | None = None
        intent_tag: str | None = None

        # --- V2 Step 1: Query 改写（开关 + 请求级 rewrite=true 双开） ---
        effective_query = query
        if (
            settings.KB_QUERY_REWRITE_ENABLED
            and rewrite
            and self._rewrite_svc is not None
        ):
            history: list[str] = []
            if settings.KB_MULTITURN_ENABLED and self._session_svc and session_id:
                history = await self._session_svc.get_recent_queries(
                    session_id, settings.SESSION_REWRITE_LOOKBACK
                )
            try:
                rewrite_result = await self._rewrite_svc.rewrite(query, history)
                if not rewrite_result.degraded:
                    effective_query = rewrite_result.rewritten_query
                    query_rewritten = rewrite_result.rewritten_query
                else:
                    query_rewritten = None  # 降级时不记录改写
            except Exception as e:
                logger.warning("rewrite_unexpected_error", error=str(e))
        elif (
            settings.KB_MULTITURN_ENABLED
            and not settings.KB_QUERY_REWRITE_ENABLED
            and self._session_svc
            and session_id
        ):
            # 多轮开启但改写关闭：fallback 规则拼接 full_query
            history = await self._session_svc.get_recent_queries(
                session_id, settings.SESSION_REWRITE_LOOKBACK
            )
            if history:
                effective_query = " ".join(history + [query])

        # --- V2 Step 2: 动态路由（开关） ---
        routing_weights: dict[str, float] | None = None
        if settings.KB_ROUTING_ENABLED and self._routing_svc is not None:
            try:
                route_result = self._routing_svc.classify_and_weight(query, category)
                intent_tag = route_result.intent_tag
                if route_result.weights:
                    routing_weights = route_result.weights
                if route_result.degraded:
                    logger.info("routing_degraded_bypass")
            except Exception as e:
                logger.warning("routing_unexpected_error", error=str(e))

        # --- Embedding（使用 effective_query） ---
        query_vector: list[float] | None = None
        with trace.step("embed", category=category, entity_ids=entity_ids or None):
            try:
                query_vector = await self.embed.embed(effective_query)
            except Exception as e:
                degraded = True
                logger.warning("embed_failed_es_only_degraded", error=str(e))

        top_n = settings.KB_RETRIEVAL_TOP_N

        # --- V2 Step 3: 双路召回（Embedding 失败时仅 ES BM25） ---
        es_kwargs = {}
        if settings.KB_SUMMARY_ENABLED:
            es_kwargs["summary_boost"] = settings.KB_SUMMARY_ES_BOOST
        if entity_ids:
            es_kwargs["entity_ids"] = entity_ids

        with trace.step("recall", top_n=top_n, category=category):
            tasks = []
            if query_vector is not None:
                tasks.append(self.milvus.search(query_vector, top_n=top_n, category=category))
            tasks.append(self.es.search(
                query=effective_query, top_n=top_n, category=category,
                **es_kwargs,
            ))
            results = await asyncio.gather(*tasks, return_exceptions=True)
        dense_hits: list[dict] = []
        sparse_hits: list[dict] = []

        es_idx = 1 if query_vector is not None else 0
        milvus_ok = query_vector is not None and not isinstance(results[0], Exception)
        es_ok = not isinstance(results[es_idx], Exception)

        if milvus_ok:
            dense_hits = results[0]
        elif query_vector is not None:
            logger.warning("milvus_search_failed", error=str(results[0]))
            degraded = True

        if es_ok:
            sparse_hits = results[es_idx]
        else:
            logger.warning("es_search_failed", error=str(results[es_idx]))
            degraded = True

        if not dense_hits and not sparse_hits:
            # 分类过滤过严时双路均为空（服务正常但无命中），自动放宽为全库再试一次
            if category and milvus_ok and es_ok and query_vector is not None:
                logger.info(
                    "category_filter_empty_retry_all",
                    category=category,
                    trace_id=trace_id,
                )
                degraded = True
                with trace.step("recall_retry_all", reason="category_empty"):
                    tasks = [
                        self.milvus.search(query_vector, top_n=top_n, category=None),
                        self.es.search(
                            query=effective_query,
                            top_n=top_n,
                            category=None,
                            **es_kwargs,
                        ),
                    ]
                    retry_results = await asyncio.gather(*tasks, return_exceptions=True)
                if not isinstance(retry_results[0], Exception):
                    dense_hits = retry_results[0]
                if not isinstance(retry_results[1], Exception):
                    sparse_hits = retry_results[1]

            milvus_failed = query_vector is not None and not milvus_ok
            es_failed = not es_ok
            if not dense_hits and not sparse_hits:
                if milvus_failed and es_failed:
                    msg = "Milvus 和 ES 均不可用，检索失败"
                elif category:
                    msg = (
                        f"分类「{category}」下无匹配结果；"
                        "请换「全部」或确认文档入库时的 category 标签"
                    )
                else:
                    msg = "知识库暂无匹配内容，检索无结果"
                raise BizException(ErrorCode.A3005, msg)

        if milvus_ok and not es_ok:
            degraded = True
        if es_ok and not milvus_ok:
            degraded = True

        # --- V2 Step 4: RRF 融合（带路由权重调整 rank） ---
        with trace.step("rrf_fuse", dense=len(dense_hits), sparse=len(sparse_hits)):
            fused = self._rrf_fuse(
                dense_hits,
                sparse_hits,
                k=settings.KB_RRF_K,
                routing_weights=routing_weights,
            )
            fused = self._dedup_by_doc_id(fused)

        # 闸门 1（对齐 step1/01、step4/02 §四.1）：召回相似度不足 → 判定无匹配，走兜底
        # 说明：RRF 融合分是排名分（量纲约 1/(k+rank)），不能直接与 0.6 比较，
        # 故以稠密召回最高相似度（Milvus IP，BGE-M3 归一化后≈cosine）作为相似度判定依据；
        # ES-only 降级（无稠密相似度）时跳过本闸门，交由闸门 2（rerank 0.35）兜底。
        max_dense_score: float | None = None
        if dense_hits:
            max_dense_score = max(float(h.get("score", 0.0)) for h in dense_hits)
        if max_dense_score is not None and max_dense_score < settings.RAG_SCORE_THRESHOLD:
            latency_ms = int((time.time() - start) * 1000)
            return await self._build_fallback(
                trace=trace,
                answer=answer,
                trace_id=trace_id,
                query=query,
                category=category,
                dense_hit_count=len(dense_hits),
                sparse_hit_count=len(sparse_hits),
                fused_count=len(fused),
                reranked_count=0,
                max_rerank_score=None,
                hit_doc_ids=list({f["doc_id"] for f in fused if f.get("doc_id")}),
                hit_chunk_ids=[f["chunk_id"] for f in fused],
                latency_ms=latency_ms,
                degraded=degraded,
                query_original=query_original,
                query_rewritten=query_rewritten,
                intent_tag=intent_tag,
                session_id=session_id,
            )

        chunk_ids = [f["chunk_id"] for f in fused]
        doc_ids = list({f["doc_id"] for f in fused if f.get("doc_id")})

        content_map: dict[str, str] = {}
        chunk_index_map: dict[str, int] = {}
        for hit in sparse_hits:
            cid = hit.get("chunk_id")
            if cid:
                content_map[cid] = hit.get("content", "")

        missing_ids = [cid for cid in chunk_ids if cid not in content_map]
        if missing_ids and self.doc_repo is not None:
            from sqlalchemy import select as sa_select
            from app.models.orm import KBChunk
            uid_list = []
            for cid in missing_ids:
                uid = _safe_uuid(cid)
                if uid is None:
                    logger.warning("recall_invalid_uuid_skipped", chunk_id=cid)
                    continue
                uid_list.append(uid)
            if uid_list:
                result = await self.doc_repo.db.execute(
                    sa_select(KBChunk).where(KBChunk.id.in_(uid_list))
                )
                for chunk in result.scalars().all():
                    content_map[str(chunk.id)] = chunk.content

        # 从 KBChunk 表补齐 chunk_index（ES/Milvus 未存储该字段）
        if self.doc_repo is not None and chunk_ids:
            from sqlalchemy import select as sa_select
            from app.models.orm import KBChunk
            uid_list = []
            for cid in chunk_ids:
                uid = _safe_uuid(cid)
                if uid is None:
                    logger.warning("recall_invalid_uuid_skipped", chunk_id=cid)
                    continue
                uid_list.append(uid)
            if uid_list:
                result = await self.doc_repo.db.execute(
                    sa_select(KBChunk).where(KBChunk.id.in_(uid_list))
                )
                for chunk in result.scalars().all():
                    chunk_index_map[str(chunk.id)] = chunk.chunk_index

        fused_with_content = []
        for f in fused:
            content = content_map.get(f["chunk_id"], "")
            fused_with_content.append({
                **f,
                "content": content,
                "chunk_index": chunk_index_map.get(f["chunk_id"], 0),
            })

        # --- V2 Step 5.5: 内容级去重（V5 修复：不同 doc_id 相同内容只保留一条） ---
        # 在既有 doc_id 去重之后、Rerank 之前执行，仅作用于输出去重阶段，不改变 RRF score 计算；
        # 避免库内多个不同 doc_id 的文档内容一字不差时，检索结果重复返回 5–6 条同内容。
        fused_with_content = self._dedup_by_content(fused_with_content)
        doc_ids = list({f["doc_id"] for f in fused_with_content if f.get("doc_id")})

        # --- V2 Step 6: 业务加权（开关，RRF→去重之后、Rerank 之前） ---
        if (
            settings.KB_WEIGHTING_ENABLED
            and self._weighting_svc is not None
            and self.doc_repo is not None
        ):
            try:
                meta_dict = await self.doc_repo.get_meta_dict(
                    [u for d in doc_ids if (u := _safe_uuid(d)) is not None]
                )
                fused_with_content = self._weighting_svc.apply(
                    fused_with_content, meta_dict
                )
            except Exception as e:
                logger.warning("weighting_unexpected_error", error=str(e))

        # --- Rerank ---
        reranked = fused_with_content
        max_rerank_score: float | None = None
        rerank_ok = False

        candidates = [
            {"chunk_id": f["chunk_id"], "content": f["content"]}
            for f in fused_with_content
            if f.get("content")
        ]
        rerank_pre_degraded = False
        try:
            rerank_pre_degraded = await self.rerank.is_degraded()
        except Exception as e:
            logger.warning("rerank_is_degraded_failed", error=str(e))
        if candidates and rerank_pre_degraded:
            degraded = True
            logger.warning("rerank_circuit_open_skip")
        with trace.step("rerank", candidates=len(candidates), degraded=rerank_pre_degraded):
            try:
                if candidates and not rerank_pre_degraded:
                    rerank_results = await self.rerank.rerank(effective_query, candidates)
                    score_map = {r["chunk_id"]: r["rerank_score"] for r in rerank_results}
                    for item in fused_with_content:
                        item["rerank_score"] = score_map.get(item["chunk_id"], 0.0)
                    reranked = sorted(
                        fused_with_content,
                        key=lambda x: x.get("rerank_score", 0) * x.get("weight", 1.0),
                        reverse=True,
                    )
                    max_rerank_score = (
                        max(
                            item.get("rerank_score", 0.0)
                            for item in fused_with_content
                        )
                        if fused_with_content
                        else None
                    )
                    rerank_ok = True
            except Exception as e:
                logger.warning("rerank_failed_degraded", error=str(e))
                degraded = True
                for item in fused_with_content:
                    item["rerank_score"] = item.get("score", 0.0)
                reranked = sorted(
                    fused_with_content, key=lambda x: x.get("rerank_score", 0), reverse=True
                )

        # 实体 ID 精确匹配后置加权（结构化查询如 CS20260020）
        if entity_ids:
            reranked = _apply_entity_boost(reranked, entity_ids)
            if reranked:
                max_rerank_score = max(float(x.get("rerank_score", 0.0)) for x in reranked)

        # --- V2 Step 7: 行级权限过滤（开关，Rerank 后） ---
        if settings.KB_ROW_LEVEL_ENABLED and self._row_access_svc is not None:
            try:
                reranked = self._row_access_svc.filter(
                    reranked, jwt_payload, category
                )
            except Exception as e:
                # V3 fail-closed：行级权限过滤异常不得静默放行（消除 fail-open 越权风险）
                logger.error(
                    "row_access_unexpected_error",
                    error=str(e),
                    trace_id=trace_id,
                )
                try:
                    default_matrix = self._row_access_svc.get_default_matrix()
                    allowed = set(
                        default_matrix.get(("default", "default"), [])
                    )
                    if category is not None:
                        allowed = allowed & {category}
                    reranked = [
                        item for item in reranked
                        if item.get("category") in allowed
                    ]
                except Exception as e2:
                    # 保底矩阵也不可用 → 置空走下游兜底闸门，绝不返回越权数据
                    logger.error(
                        "row_access_fail_closed_failed",
                        error=str(e2),
                        trace_id=trace_id,
                    )
                    reranked = []

        # 闸门 2（rerank 0.35 兜底）
        threshold = settings.KB_RERANK_THRESHOLD
        if rerank_ok and max_rerank_score is not None and max_rerank_score < threshold:
            latency_ms = int((time.time() - start) * 1000)
            return await self._build_fallback(
                trace=trace,
                answer=answer,
                trace_id=trace_id,
                query=query,
                category=category,
                dense_hit_count=len(dense_hits),
                sparse_hit_count=len(sparse_hits),
                fused_count=len(fused),
                reranked_count=len(reranked),
                max_rerank_score=max_rerank_score,
                hit_doc_ids=doc_ids,
                hit_chunk_ids=chunk_ids,
                latency_ms=latency_ms,
                degraded=degraded,
                query_original=query_original,
                query_rewritten=query_rewritten,
                intent_tag=intent_tag,
                session_id=session_id,
            )

        # 结构化实体查询（如 CS20260020）：有精确命中时优先只返回命中 chunk，减少制度类噪声
        if entity_ids:
            entity_matched = [
                r for r in reranked
                if r.get("entity_match")
                or any(eid in (r.get("content") or "") for eid in entity_ids)
            ]
            if entity_matched:
                reranked = entity_matched

        top_results = reranked[:top_k]

        titles_map: dict[str, str] = {}
        meta_map: dict[str, dict] = {}
        if self.doc_repo and doc_ids:
            uid_list = [u for d in doc_ids if (u := _safe_uuid(d)) is not None]
            titles_map = await self.doc_repo.get_titles_map(uid_list)
            # V2: 批量获取元数据用于溯源回填（version/source）
            meta_map = await self.doc_repo.get_meta_dict(uid_list)

        results_list = []
        for item in top_results:
            doc_id = item.get("doc_id", "")
            meta = meta_map.get(doc_id, {})
            results_list.append({
                "doc_id": doc_id,
                "title": titles_map.get(doc_id, ""),
                "chunk_index": item.get("chunk_index", 0),
                "content": item.get("content", ""),
                "score": item.get("score", 0.0),
                "rerank_score": item.get("rerank_score", 0.0),
                # V2 溯源字段：存量 V1 文档缺省 version=1, source="v1_legacy"
                "version": meta.get("version", 1),
                "source": meta.get("source", "v1_legacy"),
            })

        latency_ms = int((time.time() - start) * 1000)
        hit_doc_ids = [r["doc_id"] for r in results_list]
        hit_chunk_ids = [f["chunk_id"] for f in top_results]

        # --- V5: 检索响应扩展（answer / results[].assets，纯增量，缺省与 V4 一致） ---
        # LLM 回答结果（answer / citations / degraded / llm_called），缺省零调用（V4 行为）。
        llm_result = {
            "answer": None,
            "citations": [],
            "degraded": False,
            "llm_called": False,
            "llm_skip_reason": "no_results" if not results_list else None,
        }
        if results_list:
            # 1) 资产预览元数据：资产抽取开关开启且 asset_repo 可用时按 doc_id 关联填充；
            #    否则保持空数组（开关关闭零 DB 开销，响应与 V4 完全一致）。
            if (
                settings.KB_IMAGE_EXTRACT_ENABLED or settings.KB_TABLE_EXTRACT_ENABLED
            ) and self._asset_repo is not None:
                try:
                    assets_map = await self._get_assets_map(hit_doc_ids)
                except Exception as e:
                    logger.warning("assets_fill_failed", error=str(e), trace_id=trace_id)
                    assets_map = {}
                for item in results_list:
                    item["assets"] = assets_map.get(item["doc_id"], [])
            else:
                for item in results_list:
                    item["assets"] = []

            # 2) LLM 回答：llm_service 可用时按门控调用（开关/answer/非兜底三闸门
            #    由 LLMService 内部判定）；失败一律降级 answer=null 且 degraded=true，不抛错。
            #    context 项含 content/doc_id（chunk 级无页码数据源，页码仅资产级，不注入）。
            if self._llm_svc is not None:
                context = [
                    {
                        "content": it.get("content", ""),
                        "doc_id": it.get("doc_id", ""),
                        "category": it.get("category"),
                        "source": (
                            meta_map.get(it.get("doc_id", ""), {}).get("source")
                            or it.get("source")
                        ),
                    }
                    for it in top_results
                ]
                try:
                    with trace.step(
                        "llm_answer",
                        answer_requested=answer,
                        enabled=settings.KB_LLM_ANSWER_ENABLED,
                    ):
                        llm_result = await self._llm_svc.generate_answer(
                            query, context, answer_requested=answer
                        )
                    if llm_result["degraded"]:
                        degraded = True
                except Exception as e:
                    logger.warning(
                        "llm_unexpected_error", error=str(e), trace_id=trace_id
                    )
                    llm_result = {
                        "answer": None,
                        "citations": [],
                        "degraded": True,
                        "llm_called": True,
                        "llm_skip_reason": f"unexpected:{type(e).__name__}",
                    }
                    degraded = True

        trace_dict = trace.finish(
            outputs={
                "dense_hits": len(dense_hits),
                "sparse_hits": len(sparse_hits),
                "results": len(results_list),
                "max_rerank_score": max_rerank_score,
                "llm_called": llm_result.get("llm_called"),
            }
        )

        await self.logger.log(
            trace_id=trace_id, query=query, category=category,
            dense_hit_count=len(dense_hits), sparse_hit_count=len(sparse_hits),
            fused_count=len(fused), reranked_count=len(reranked),
            max_rerank_score=max_rerank_score,
            hit_doc_ids=hit_doc_ids, hit_chunk_ids=hit_chunk_ids,
            latency_ms=latency_ms, degraded=degraded, is_fallback=False,
            cache_hit="0",
            query_original=query_original,
            query_rewritten=query_rewritten,
            intent_tag=intent_tag,
            session_id=session_id,
            # V5: LLM 回答调用/降级标记（缺省 '0'，存量行为不变）
            llm_called="1" if llm_result["llm_called"] else "0",
            llm_degraded="1" if llm_result["degraded"] else "0",
        )

        # V2: 多轮会话记录（开关）
        if settings.KB_MULTITURN_ENABLED and self._session_svc and session_id:
            try:
                await self._session_svc.record(
                    session_id=session_id,
                    query=query,
                    rewritten_query=query_rewritten,
                    top_doc_ids=hit_doc_ids,
                )
            except Exception as e:
                # V3 容错：会话记录失败仅 warning，不阻断检索返回（消除 Redis 故障连锁 500）
                logger.warning("session_record_unexpected_error", error=str(e))

        return {
            "results": results_list,
            "degraded": degraded,
            "query_original": query,
            "message": None,
            "trace_id": trace_id,
            "answer": llm_result["answer"],
            "citations": llm_result["citations"],
            "trace": trace_dict,
            "llm_meta": {
                "requested": answer,
                "enabled": settings.KB_LLM_ANSWER_ENABLED,
                "provider": settings.LLM_PROVIDER,
                "called": llm_result.get("llm_called", False),
                "skip_reason": llm_result.get("llm_skip_reason"),
            },
        }

    async def _get_assets_map(self, doc_ids: list[str]) -> dict[str, list[dict]]:
        """按 doc_id 批量关联内嵌资产预览元数据（asset_type/page/storage_path/mime_type）。

        - 非法 UUID 跳过（召回脏数据不阻断，对齐 _safe_uuid 既有语义）；
        - 无资产的 doc_id 不写入映射，调用方缺省取空数组。
        """
        assets_map: dict[str, list[dict]] = {}
        for doc_id in doc_ids:
            uid = _safe_uuid(doc_id)
            if uid is None:
                continue
            try:
                assets = await self._asset_repo.get_by_doc(uid)
            except Exception as e:
                logger.warning(
                    "assets_get_by_doc_failed",
                    doc_id=doc_id,
                    error=str(e),
                    trace_id=get_trace_id(),
                )
                continue
            if assets:
                assets_map[doc_id] = [
                    {
                        "asset_type": a.asset_type,
                        "page": a.page,
                        "storage_path": a.storage_path,
                        "mime_type": a.mime_type,
                    }
                    for a in assets
                ]
        return assets_map

    async def _build_fallback(
        self,
        *,
        trace: SearchTraceCollector | None = None,
        answer: bool = False,
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
        query_original: str | None = None,
        query_rewritten: str | None = None,
        intent_tag: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """公共兜底路径：落检索日志 + 返回兜底响应（闸门 1/2 共用）。"""
        await self.logger.log(
            trace_id=trace_id,
            query=query,
            category=category,
            dense_hit_count=dense_hit_count,
            sparse_hit_count=sparse_hit_count,
            fused_count=fused_count,
            reranked_count=reranked_count,
            max_rerank_score=max_rerank_score,
            hit_doc_ids=hit_doc_ids,
            hit_chunk_ids=hit_chunk_ids,
            latency_ms=latency_ms,
            degraded=degraded,
            is_fallback=True,
            cache_hit="0",
            query_original=query_original,
            query_rewritten=query_rewritten,
            intent_tag=intent_tag,
            session_id=session_id,
        )
        trace_dict = (
            trace.finish(
                outputs={"fallback": True, "max_rerank_score": max_rerank_score}
            )
            if trace is not None
            else {
                "trace_id": trace_id,
                "total_ms": latency_ms,
                "steps": [{"name": "fallback", "latency_ms": latency_ms}],
            }
        )
        return {
            "results": [],
            "degraded": degraded,
            "query_original": query,
            "message": _FALLBACK_MESSAGE,
            "trace_id": trace_id,
            "answer": None,
            "citations": [],
            "trace": trace_dict,
            "llm_meta": {
                "requested": answer,
                "enabled": settings.KB_LLM_ANSWER_ENABLED,
                "provider": settings.LLM_PROVIDER,
                "called": False,
                "skip_reason": "search_fallback",
            },
        }

    def _rrf_fuse(
        self,
        dense_hits: list[dict],
        sparse_hits: list[dict],
        k: int = 60,
        routing_weights: dict[str, float] | None = None,
    ) -> list[dict]:
        """RRF 融合，可选路由权重调整 rank（adjusted_rank = rank / weight）。

        - routing_weights 为 None 或空时，weight=1.0（V1 行为）。
        - 权重按每个 hit 的 category 字段查找；category 缺失或未命中权重表 → 1.0。
        """
        scores: dict[str, float] = {}
        meta: dict[str, dict] = {}

        def _weight_for(cat: str | None) -> float:
            if not routing_weights or not cat:
                return 1.0
            return routing_weights.get(cat, 1.0)

        for rank, hit in enumerate(dense_hits, 1):
            cid = hit.get("chunk_id")
            if not cid:
                continue
            weight = _weight_for(hit.get("category"))
            adjusted_rank = rank / weight
            rrf_score = 1.0 / (k + adjusted_rank)
            scores[cid] = scores.get(cid, 0.0) + rrf_score
            if cid not in meta:
                meta[cid] = {
                    "chunk_id": cid,
                    "doc_id": hit.get("doc_id", ""),
                    "category": hit.get("category"),
                }

        for rank, hit in enumerate(sparse_hits, 1):
            cid = hit.get("chunk_id")
            if not cid:
                continue
            weight = _weight_for(hit.get("category"))
            adjusted_rank = rank / weight
            rrf_score = 1.0 / (k + adjusted_rank)
            scores[cid] = scores.get(cid, 0.0) + rrf_score
            if cid not in meta:
                meta[cid] = {
                    "chunk_id": cid,
                    "doc_id": hit.get("doc_id", ""),
                    "category": hit.get("category"),
                }

        fused = [
            {**meta[cid], "score": scores[cid]}
            for cid in sorted(scores, key=lambda x: scores[x], reverse=True)
        ]
        return fused

    def _dedup_by_doc_id(self, fused: list[dict]) -> list[dict]:
        """同 doc_id 最高分去重（强制无开关，始终生效）。

        遍历 RRF 融合后的 items，按 doc_id 分组保留得分最高的 chunk，
        保证输出 doc_id 唯一。无 doc_id 的项也会被保留（key="").
        """
        by_doc: dict[str, dict] = {}
        for item in fused:
            doc_id = item.get("doc_id", "")
            if (
                doc_id not in by_doc
                or item.get("score", 0.0) > by_doc[doc_id].get("score", 0.0)
            ):
                by_doc[doc_id] = item
        return list(by_doc.values())

    @staticmethod
    def _normalize_content(text: str) -> str:
        """用于内容级去重的规范化：去空白、统一大小写与全半角标点。

        - NFKC 归一化：把全角字符/标点映射为半角，统一全半角差异；
        - 去除所有空白（含中英文空格、制表符、换行）；
        - casefold：统一大小写（含特殊语言字符）。

        返回空串表示该内容无可提取文本，不参与内容去重。
        """
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"\s+", "", normalized)
        return normalized.casefold()

    def _dedup_by_content(self, items: list[dict]) -> list[dict]:
        """内容级去重（V5 修复）：不同 doc_id 但 content 相同/仅格式差异只保留一条。

        在既有 doc_id 去重之后、Rerank 之前执行，仅作用于输出去重阶段，不改变 RRF score 计算。
        对 content 做规范化后比较，相同或仅格式差异（空格/大小写/全半角标点）只保留排序靠前
        （score 更高）的一条；无可提取内容的项原样保留。
        """
        seen: dict[str, dict] = {}
        for item in items:
            key = self._normalize_content(item.get("content", "") or "")
            if not key:
                continue
            current = seen.get(key)
            if current is None or item.get("score", 0.0) > current.get("score", 0.0):
                seen[key] = item

        deduped: list[dict] = []
        for item in items:
            key = self._normalize_content(item.get("content", "") or "")
            if not key or seen.get(key) is item:
                deduped.append(item)
        return deduped
