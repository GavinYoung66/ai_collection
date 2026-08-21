"""Elasticsearch 客户端：BM25 索引管理 + 文档增删查。"""
import structlog
from elasticsearch import AsyncElasticsearch

from common.config.settings import settings

logger = structlog.get_logger()


class ESClient:
    INDEX_NAME = "kb_bm25"

    def __init__(self, es_url: str | None = None):
        self.es_url = es_url or settings.es_url
        self._client: AsyncElasticsearch | None = None

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
                        }
                    }
                }
                await client.indices.create(index=self.INDEX_NAME, body=fallback_mapping)
                logger.info("es_index_created", index=self.INDEX_NAME, analyzer="standard")
            else:
                raise

    async def index_docs(self, chunks: list[dict]) -> None:
        client = await self._get_client()
        actions = []
        for chunk in chunks:
            actions.append({"index": {"_index": self.INDEX_NAME, "_id": chunk["chunk_id"]}})
            actions.append({
                "chunk_id": chunk["chunk_id"],
                "content": chunk["content"],
                "doc_id": chunk["doc_id"],
                "category": chunk.get("category", "other"),
            })
        if actions:
            await client.bulk(operations=actions)
            await client.indices.refresh(index=self.INDEX_NAME)

    async def search(
        self, query: str, top_n: int = 20, category: str | None = None
    ) -> list[dict]:
        client = await self._get_client()
        filter_clause = []
        if category:
            filter_clause.append({"term": {"category": category}})

        body = {
            "size": top_n,
            "query": {
                "bool": {
                    "must": [{"match": {"content": query}}],
                    **({"filter": filter_clause} if filter_clause else {}),
                }
            },
        }
        result = await client.search(index=self.INDEX_NAME, body=body)
        hits = []
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            hits.append({
                "chunk_id": source["chunk_id"],
                "doc_id": source["doc_id"],
                "category": source.get("category"),
                "content": source.get("content", ""),
                "score": hit["_score"],
            })
        return hits

    async def delete_by_doc(self, doc_id: str) -> None:
        client = await self._get_client()
        await client.delete_by_query(
            index=self.INDEX_NAME,
            body={"query": {"term": {"doc_id": doc_id}}},
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
