"""Milvus 客户端：collection 管理 + 向量增删查。"""
import asyncio
import uuid as uuid_module

import structlog
from pymilvus import (
    Collection,
    CollectionSchema,
    connections,
    DataType,
    FieldSchema,
    utility,
)

from common.config.settings import settings

logger = structlog.get_logger()


def uuid_to_int64(uuid_str: str) -> int:
    """UUID → INT64：取前 8 字节的无符号整数。"""
    u = uuid_module.UUID(uuid_str)
    return int.from_bytes(u.bytes[:8], "big") & 0x7FFFFFFFFFFFFFFF


class MilvusClient:
    COLLECTION_NAME = "kb_vectors"
    DIM = 1024

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or settings.MILVUS_HOST
        self.port = port or settings.MILVUS_PORT
        self._connected = False
        self._collection: Collection | None = None

    def _connect(self) -> None:
        if not self._connected:
            connections.connect(
                alias="default",
                host=self.host,
                port=str(self.port),
            )
            self._connected = True

    async def ensure_collection(self) -> None:
        def _ensure() -> None:
            self._connect()
            if utility.has_collection(self.COLLECTION_NAME):
                self._collection = Collection(self.COLLECTION_NAME)
                return

            fields = [
                FieldSchema(name="chunk_id", dtype=DataType.INT64, is_primary=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.DIM),
                FieldSchema(name="doc_id", dtype=DataType.INT64),
                FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=50),
                FieldSchema(name="chunk_id_str", dtype=DataType.VARCHAR, max_length=36),
                FieldSchema(name="doc_id_str", dtype=DataType.VARCHAR, max_length=36),
            ]
            schema = CollectionSchema(fields, "RAG knowledge base vectors")
            self._collection = Collection(self.COLLECTION_NAME, schema)
            self._collection.create_index(
                field_name="vector",
                index_params={
                    "index_type": "IVF_FLAT",
                    "metric_type": "IP",
                    "params": {"nlist": 1024},
                },
            )
            logger.info("milvus_collection_created", collection=self.COLLECTION_NAME)

        await asyncio.to_thread(_ensure)

    async def insert(
        self, vectors: list[list[float]], metadata: list[dict]
    ) -> list[str]:
        def _insert() -> list[str]:
            self._connect()
            col = self._collection or Collection(self.COLLECTION_NAME)
            chunk_ids = [uuid_to_int64(m["chunk_id"]) for m in metadata]
            doc_ids = [uuid_to_int64(m["doc_id"]) for m in metadata]
            categories = [m.get("category", "other") for m in metadata]
            chunk_id_strs = [m["chunk_id"] for m in metadata]
            doc_id_strs = [m["doc_id"] for m in metadata]
            col.insert([
                chunk_ids,
                vectors,
                doc_ids,
                categories,
                chunk_id_strs,
                doc_id_strs,
            ])
            col.flush()
            return [str(cid) for cid in chunk_ids]

        return await asyncio.to_thread(_insert)

    async def search(
        self, query_vector: list[float], top_n: int = 20,
        category: str | None = None,
    ) -> list[dict]:
        def _search() -> list[dict]:
            self._connect()
            col = self._collection or Collection(self.COLLECTION_NAME)
            col.load()
            expr = f'category == "{category}"' if category else ""
            results = col.search(
                data=[query_vector],
                anns_field="vector",
                param={"metric_type": "IP", "params": {"nprobe": 16}},
                limit=top_n,
                expr=expr,
                output_fields=["chunk_id_str", "doc_id_str", "category"],
            )
            hits = []
            for hit in results[0]:
                hits.append({
                    "chunk_id": hit.entity.get("chunk_id_str"),
                    "doc_id": hit.entity.get("doc_id_str"),
                    "category": hit.entity.get("category"),
                    "score": hit.score,
                })
            return hits

        return await asyncio.to_thread(_search)

    async def delete_by_doc(self, doc_id: str) -> None:
        def _delete() -> None:
            self._connect()
            col = self._collection or Collection(self.COLLECTION_NAME)
            try:
                col.load()
            except Exception:
                pass  # collection 可能已加载或未创建，忽略
            doc_id_int = uuid_to_int64(doc_id)
            col.delete(expr=f"doc_id == {doc_id_int}")
            col.flush()

        await asyncio.to_thread(_delete)
