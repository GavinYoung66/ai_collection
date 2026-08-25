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
import time
import uuid

import structlog

from app.repositories.doc_repo import DocRepo
from app.services.log_service import LogService
from app.services.query_rewrite_service import RewriteService
from app.services.routing_service import RoutingService
from app.services.row_access_service import RowAccessService
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

    async def hybrid_search(
        self,
        query: str,
        category: str | None = None,
        top_k: int = 5,
        # V2 参数
        session_id: str | None = None,
        rewrite: bool = False,
        jwt_payload: dict | None = None,
    ) -> dict:
        start = time.time()
        trace_id = get_trace_id()
        degraded = False

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
        # 修复：Embedding 失败（熔断/超时/宕机）→ 降级 ES-only（仅 BM25 召回），
        # 对齐 hybrid-search spec 闸门 1 约定：dense_hits 空、max_dense_score=None 时
        # 闸门 1 短路跳过，交由闸门 2（rerank 0.35）兜底；双路均不可用才抛 A3005。
        query_vector: list[float] | None = None
        try:
            query_vector = await self.embed.embed(effective_query)
        except Exception as e:
            degraded = True
            logger.warning("embed_failed_es_only_degraded", error=str(e))

        top_n = settings.KB_RETRIEVAL_TOP_N

        # --- V2 Step 3: 双路召回（Embedding 失败时仅 ES BM25） ---
        tasks = []
        if query_vector is not None:
            tasks.append(self.milvus.search(query_vector, top_n=top_n, category=category))
        # V4: 摘要增强开关开启时对 doc_summary 字段 BM25 加权（权重 KB_SUMMARY_ES_BOOST）；
        # 开关关闭时 summary_boost=None → 不传该 kwarg，ES 调用签名与 V3 完全一致（向后兼容既有调用方）
        es_kwargs = {}
        if settings.KB_SUMMARY_ENABLED:
            es_kwargs["summary_boost"] = settings.KB_SUMMARY_ES_BOOST
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
            raise BizException(ErrorCode.A3005, "Milvus 和 ES 均不可用，检索失败")

        if milvus_ok and not es_ok:
            degraded = True
        if es_ok and not milvus_ok:
            degraded = True

        # --- V2 Step 4: RRF 融合（带路由权重调整 rank） ---
        fused = self._rrf_fuse(
            dense_hits,
            sparse_hits,
            k=settings.KB_RRF_K,
            routing_weights=routing_weights,
        )

        # --- V2 Step 5: 同 doc_id 去重（强制无开关，始终生效） ---
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
            uid_list = [uuid.UUID(cid) for cid in missing_ids]
            result = await self.doc_repo.db.execute(
                sa_select(KBChunk).where(KBChunk.id.in_(uid_list))
            )
            for chunk in result.scalars().all():
                content_map[str(chunk.id)] = chunk.content

        # 从 KBChunk 表补齐 chunk_index（ES/Milvus 未存储该字段）
        if self.doc_repo is not None and chunk_ids:
            from sqlalchemy import select as sa_select
            from app.models.orm import KBChunk
            uid_list = [uuid.UUID(cid) for cid in chunk_ids]
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

        # --- V2 Step 6: 业务加权（开关，RRF→去重之后、Rerank 之前） ---
        if (
            settings.KB_WEIGHTING_ENABLED
            and self._weighting_svc is not None
            and self.doc_repo is not None
        ):
            try:
                meta_dict = await self.doc_repo.get_meta_dict(
                    [uuid.UUID(d) for d in doc_ids if d]
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
            # V3 容错：熔断探测失败（Redis 不可用）按"未熔断"处理，避免连锁 500
            logger.warning("rerank_is_degraded_failed", error=str(e))
        if candidates and rerank_pre_degraded:
            # 熔断断开期：跳过 rerank 用 RRF score 排序，标记降级
            degraded = True
            logger.warning("rerank_circuit_open_skip")
        try:
            if candidates and not rerank_pre_degraded:
                rerank_results = await self.rerank.rerank(effective_query, candidates)
                score_map = {r["chunk_id"]: r["rerank_score"] for r in rerank_results}
                for item in fused_with_content:
                    item["rerank_score"] = score_map.get(item["chunk_id"], 0.0)
                # D2 修复：业务加权在 rerank 主路径生效——排序分 = rerank_score × weight；
                # D-1 修复：max_rerank_score 取全局最大原始 rerank_score（不乘权重），
                # 使闸门 2 的 0.35 兜底阈值语义不被加权排序污染（加权项居首≠原始分最高）。
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
            # Rerank 宕机：按 DD-03 降级矩阵用 RRF score 排序返回结果（degraded），
            # RRF 分数与 rerank 分数不同量纲，不参与 0.35 兜底阈值判断
            logger.warning("rerank_failed_degraded", error=str(e))
            degraded = True
            for item in fused_with_content:
                item["rerank_score"] = item.get("score", 0.0)
            reranked = sorted(
                fused_with_content, key=lambda x: x.get("rerank_score", 0), reverse=True
            )

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

        top_results = reranked[:top_k]

        titles_map: dict[str, str] = {}
        meta_map: dict[str, dict] = {}
        if self.doc_repo and doc_ids:
            uid_list = [uuid.UUID(d) for d in doc_ids]
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
        }

    async def _build_fallback(
        self,
        *,
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
        return {
            "results": [],
            "degraded": degraded,
            "query_original": query,
            "message": _FALLBACK_MESSAGE,
            "trace_id": trace_id,
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
