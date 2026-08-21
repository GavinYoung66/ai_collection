"""混合检索编排：向量召回 + BM25 召回 → RRF 融合 → Rerank → 兜底。"""
import asyncio
import time
import uuid

import structlog

from app.repositories.doc_repo import DocRepo
from app.services.log_service import LogService
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
    ):
        self.milvus = milvus_client
        self.es = es_client
        self.embed = embed_client
        self.rerank = rerank_client
        self.logger = log_service
        self.doc_repo = doc_repo

    async def hybrid_search(
        self, query: str, category: str | None = None, top_k: int = 5
    ) -> dict:
        start = time.time()
        trace_id = get_trace_id()
        degraded = False

        try:
            query_vector = await self.embed.embed(query)
        except Exception as e:
            logger.error("embed_query_failed", error=str(e))
            raise BizException(ErrorCode.A3003, f"Embedding 服务不可用: {e}")

        top_n = settings.KB_RETRIEVAL_TOP_N

        milvus_task = self.milvus.search(query_vector, top_n=top_n, category=category)
        es_task = self.es.search(query=query, top_n=top_n, category=category)

        results = await asyncio.gather(milvus_task, es_task, return_exceptions=True)
        dense_hits: list[dict] = []
        sparse_hits: list[dict] = []

        milvus_ok = not isinstance(results[0], Exception)
        es_ok = not isinstance(results[1], Exception)

        if milvus_ok:
            dense_hits = results[0]
        else:
            logger.warning("milvus_search_failed", error=str(results[0]))
            degraded = True

        if es_ok:
            sparse_hits = results[1]
        else:
            logger.warning("es_search_failed", error=str(results[1]))
            degraded = True

        if not dense_hits and not sparse_hits:
            raise BizException(ErrorCode.A3005, "Milvus 和 ES 均不可用，检索失败")

        if milvus_ok and not es_ok:
            degraded = True
        if es_ok and not milvus_ok:
            degraded = True

        fused = self._rrf_fuse(dense_hits, sparse_hits, k=settings.KB_RRF_K)

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

        reranked = fused_with_content
        max_rerank_score: float | None = None
        rerank_ok = False

        candidates = [
            {"chunk_id": f["chunk_id"], "content": f["content"]}
            for f in fused_with_content
            if f.get("content")
        ]
        rerank_pre_degraded = await self.rerank.is_degraded()
        if candidates and rerank_pre_degraded:
            # 熔断断开期：跳过 rerank 用 RRF 排序，标记降级
            degraded = True
            logger.warning("rerank_circuit_open_skip")
        try:
            if candidates and not rerank_pre_degraded:
                rerank_results = await self.rerank.rerank(query, candidates)
                score_map = {r["chunk_id"]: r["rerank_score"] for r in rerank_results}
                for item in fused_with_content:
                    item["rerank_score"] = score_map.get(item["chunk_id"], 0.0)
                reranked = sorted(
                    fused_with_content, key=lambda x: x.get("rerank_score", 0), reverse=True
                )
                max_rerank_score = reranked[0]["rerank_score"] if reranked else None
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
            )

        top_results = reranked[:top_k]

        titles_map: dict[str, str] = {}
        if self.doc_repo and doc_ids:
            uid_list = [uuid.UUID(d) for d in doc_ids]
            titles_map = await self.doc_repo.get_titles_map(uid_list)

        results_list = []
        for item in top_results:
            results_list.append({
                "doc_id": item.get("doc_id", ""),
                "title": titles_map.get(item.get("doc_id", ""), ""),
                "chunk_index": item.get("chunk_index", 0),
                "content": item.get("content", ""),
                "score": item.get("score", 0.0),
                "rerank_score": item.get("rerank_score", 0.0),
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
        )

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
        )
        return {
            "results": [],
            "degraded": degraded,
            "query_original": query,
            "message": _FALLBACK_MESSAGE,
            "trace_id": trace_id,
        }

    def _rrf_fuse(
        self, dense_hits: list[dict], sparse_hits: list[dict], k: int = 60
    ) -> list[dict]:
        scores: dict[str, float] = {}
        meta: dict[str, dict] = {}

        for rank, hit in enumerate(dense_hits, 1):
            cid = hit.get("chunk_id")
            if not cid:
                continue
            rrf_score = 1.0 / (k + rank)
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
            rrf_score = 1.0 / (k + rank)
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
