"""Elasticsearch 客户端：BM25 索引管理 + 文档增删查。

V2 增量：ES_DUAL_WRITE=true 时同时写入 kb_bm25（V1）和 kb_chunks_bm25（V2），
读操作始终走 ES_READ_INDEX（默认 kb_bm25，发布中期切换为 kb_chunks_bm25）。

V4 增量：kb_bm25 增补 doc_summary 字段（text, ik_max_word，摘要检索副本）——
index_docs 写入 doc_summary（缺省空）、search 回读 doc_summary、
update_summary 幂等更新 doc_summary（供补全接口复用）。字段为增量，V3 缺省行为不变。
"""
import os

import structlog
from elasticsearch import AsyncElasticsearch

from common.config.settings import settings

logger = structlog.get_logger()


class ESClient:
    INDEX_NAME = "kb_bm25"
    INDEX_NAME_V2 = "kb_chunks_bm25"

    def __init__(self, es_url: str | None = None):
        self.es_url = es_url or settings.es_url
        self._client: AsyncElasticsearch | None = None
        # 读索引由环境变量控制（默认 V1 索引，发布中期切换 V2）
        self._read_index = os.environ.get("ES_READ_INDEX", self.INDEX_NAME)

    @property
    def _dual_write(self) -> bool:
        return settings.ES_DUAL_WRITE

    async def _get_client(self) -> AsyncElasticsearch:
        if self._client is None:
            self._client = AsyncElasticsearch(self.es_url)
        return self._client

    async def ensure_index(self) -> None:
        client = await self._get_client()
        exists = await client.indices.exists(index=self.INDEX_NAME)
        if exists:
            return

        mapping = {
            "settings": {
                "analysis": {
                    "analyzer": {
                        "ik_analyzer": {"type": "ik_max_word"},
                        "ik_search_analyzer": {"type": "ik_smart"},
                    }
                }
            },
            "mappings": {
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "content": {
                        "type": "text",
                        "analyzer": "ik_analyzer",
                        "search_analyzer": "ik_search_analyzer",
                    },
                    "doc_id": {"type": "keyword"},
                    "category": {"type": "keyword"},
                    # V4 增量：摘要检索副本（文档级摘要，参与 BM25 加权）
                    "doc_summary": {
                        "type": "text",
                        "analyzer": "ik_analyzer",
                        "search_analyzer": "ik_search_analyzer",
                    },
                }
            },
        }

        try:
            await client.indices.create(index=self.INDEX_NAME, body=mapping)
            logger.info("es_index_created", index=self.INDEX_NAME, analyzer="ik_max_word")
        except Exception as e:
            if "analyzer" in str(e).lower() or "ik" in str(e).lower():
                logger.warning("es_ik_not_available_fallback", error=str(e))
                fallback_mapping = {
                    "mappings": {
                        "properties": {
                            "chunk_id": {"type": "keyword"},
                            "content": {"type": "text"},
                            "doc_id": {"type": "keyword"},
                            "category": {"type": "keyword"},
                            # V4 增量：摘要检索副本（standard 分词回退）
                            "doc_summary": {"type": "text"},
                        }
                    }
                }
                await client.indices.create(index=self.INDEX_NAME, body=fallback_mapping)
                logger.info("es_index_created", index=self.INDEX_NAME, analyzer="standard")
            else:
                raise

    async def index_docs(self, chunks: list[dict]) -> None:
        client = await self._get_client()
        # V1 写入
        actions = []
        for chunk in chunks:
            actions.append({"index": {"_index": self.INDEX_NAME, "_id": chunk["chunk_id"]}})
            actions.append({
                "chunk_id": chunk["chunk_id"],
                "content": chunk["content"],
                "doc_id": chunk["doc_id"],
                "category": chunk.get("category", "other"),
                # V4 增量：文档级摘要检索副本（缺省空，存量 chunk 无摘要字段）
                "doc_summary": chunk.get("doc_summary", ""),
            })
        if actions:
            await client.bulk(operations=actions)
            await client.indices.refresh(index=self.INDEX_NAME)

        # V2 双写
        if self._dual_write:
            v2_actions = []
            for chunk in chunks:
                v2_actions.append({"index": {"_index": self.INDEX_NAME_V2, "_id": chunk["chunk_id"]}})
                v2_actions.append({
                    "chunk_id": chunk["chunk_id"],
                    "content": chunk["content"],
                    "doc_id": chunk["doc_id"],
                    "category": chunk.get("category", "other"),
                    "version": chunk.get("version", 1),
                    "source": chunk.get("source", "v1_legacy"),
                    "customer_id": chunk.get("customer_id"),
                    "agent_id": chunk.get("agent_id"),
                    "risk_tag": chunk.get("risk_tag"),
                    # V4 增量：文档级摘要检索副本（缺省空）
                    "doc_summary": chunk.get("doc_summary", ""),
                })
            if v2_actions:
                await client.bulk(operations=v2_actions)
                await client.indices.refresh(index=self.INDEX_NAME_V2)

    async def search(
        self, query: str, top_n: int = 20, category: str | None = None,
        summary_boost: float | None = None,
    ) -> list[dict]:
        """BM25 检索。

        V4 增量：summary_boost 不为 None 且 > 0 时，对 content（must，保持 V3 召回语义）
        + doc_summary（should，加权上浮）双字段检索；缺省 None = 开关关闭，query 与 V3 完全一致。
        """
        client = await self._get_client()
        filter_clause = []
        if category:
            filter_clause.append({"term": {"category": category}})

        if summary_boost is not None and summary_boost > 0:
            # V4 摘要加权：正文 must 保持召回不变量，摘要 should 加权上浮（命中摘要者排序提升）
            must = [{"match": {"content": {"query": query, "boost": 1.0}}}]
            should = [
                {"match": {"doc_summary": {"query": query, "boost": summary_boost}}}
            ]
            query_clause = {
                "bool": {
                    "must": must,
                    "should": should,
                    **({"filter": filter_clause} if filter_clause else {}),
                }
            }
        else:
            # 开关关闭（默认）→ 与 V3 完全一致
            query_clause = {
                "bool": {
                    "must": [{"match": {"content": query}}],
                    **({"filter": filter_clause} if filter_clause else {}),
                }
            }

        body = {
            "size": top_n,
            "query": query_clause,
        }
        result = await client.search(index=self._read_index, body=body)
        hits = []
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            hits.append({
                "chunk_id": source["chunk_id"],
                "doc_id": source["doc_id"],
                "category": source.get("category"),
                "content": source.get("content", ""),
                # V4 增量：摘要检索副本回读（缺省空）
                "doc_summary": source.get("doc_summary", ""),
                "score": hit["_score"],
            })
        return hits

    async def update_summary(self, doc_id: str, summary: str) -> None:
        """V4 增量：幂等更新文档级摘要 doc_summary（按 doc_id 重写）。

        同时更新 kb_bm25 与 kb_chunks_bm25 两个索引，ignore_unavailable
        兼容索引缺失场景；对不存在 doc_summary 字段的存量 chunk 亦生效。
        供摘要补全接口（PUT /kb/documents/{doc_id}/summary）复用。
        """
        client = await self._get_client()
        for index in {self.INDEX_NAME, self.INDEX_NAME_V2}:
            await client.update_by_query(
                index=index,
                body={
                    "query": {"term": {"doc_id": doc_id}},
                    "script": {
                        "source": "ctx._source.doc_summary = params.s",
                        "params": {"s": summary},
                    },
                },
                refresh=True,
                ignore_unavailable=True,
            )
        logger.info("es_summary_updated", doc_id=doc_id)

    async def delete_by_doc(self, doc_id: str) -> None:
        client = await self._get_client()
        await client.delete_by_query(
            index=self.INDEX_NAME,
            body={"query": {"term": {"doc_id": doc_id}}},
        )
        if self._dual_write:
            await client.delete_by_query(
                index=self.INDEX_NAME_V2,
                body={"query": {"term": {"doc_id": doc_id}}},
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
