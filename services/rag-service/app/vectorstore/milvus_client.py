"""Milvus 客户端：collection 管理 + 向量增删查。

V2 增量：若 kb_vectors_v2 collection 存在则自动切换到 v2 schema
（含 version INT64 / source VARCHAR(255) 字段），否则保持 V1 行为。
v2 collection 由 scripts/rag/rebuild_milvus_v2.py 创建并 collection 名探测自动切换。
"""
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
    COLLECTION_NAME_V2 = "kb_vectors_v2"
    DIM = 1024

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or settings.MILVUS_HOST
        self.port = port or settings.MILVUS_PORT
        self._connected = False
        self._collection: Collection | None = None
        self._use_v2: bool = False

    def _connect(self) -> None:
        if not self._connected:
            connections.connect(
                alias="default",
                host=self.host,
                port=str(self.port),
            )
            self._connected = True

    @property
    def _active_name(self) -> str:
        """当前生效的 collection 名（v2 优先，回退 v1）。"""
        return self.COLLECTION_NAME_V2 if self._use_v2 else self.COLLECTION_NAME

    async def ensure_collection(self) -> None:
        def _ensure() -> None:
            self._connect()
            # V2: 检查 kb_vectors_v2 是否存在，存在则切换
            if utility.has_collection(self.COLLECTION_NAME_V2):
                self._collection = Collection(self.COLLECTION_NAME_V2)
                self._use_v2 = True
                logger.info("milvus_using_v2_collection", collection=self.COLLECTION_NAME_V2)
                return
            # V1 回退
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
            col = self._collection or Collection(self._active_name)
            chunk_ids = [uuid_to_int64(m["chunk_id"]) for m in metadata]
            doc_ids = [uuid_to_int64(m["doc_id"]) for m in metadata]
            categories = [m.get("category", "other") for m in metadata]
            chunk_id_strs = [m["chunk_id"] for m in metadata]
            doc_id_strs = [m["doc_id"] for m in metadata]

            if self._use_v2:
                # V2 schema: 含 version / source 字段
                versions = [int(m.get("version", 1)) for m in metadata]
                sources = [str(m.get("source", "v1_legacy")) for m in metadata]
                col.insert([
                    chunk_ids,
                    vectors,
                    doc_ids,
                    categories,
                    chunk_id_strs,
                    doc_id_strs,
                    versions,
                    sources,
                ])
            else:
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
            col = self._collection or Collection(self._active_name)
            col.load()
            expr = f'category == "{category}"' if category else ""
            output_fields = ["chunk_id_str", "doc_id_str", "category"]
            if self._use_v2:
                output_fields.extend(["version", "source"])
            results = col.search(
                data=[query_vector],
                anns_field="vector",
                param={"metric_type": "IP", "params": {"nprobe": 16}},
                limit=top_n,
                expr=expr,
                output_fields=output_fields,
            )
            hits = []
            for hit in results[0]:
                entry = {
                    "chunk_id": hit.entity.get("chunk_id_str"),
                    "doc_id": hit.entity.get("doc_id_str"),
                    "category": hit.entity.get("category"),
                    "score": hit.score,
                }
                if self._use_v2:
                    entry["version"] = hit.entity.get("version", 1)
                    entry["source"] = hit.entity.get("source", "v1_legacy")
                hits.append(entry)
            return hits

        return await asyncio.to_thread(_search)

    async def delete_by_doc(self, doc_id: str) -> None:
        def _delete() -> None:
            self._connect()
            col = self._collection or Collection(self._active_name)
            try:
                col.load()
            except Exception:
                pass  # collection 可能已加载或未创建，忽略
            doc_id_int = uuid_to_int64(doc_id)
            col.delete(expr=f"doc_id == {doc_id_int}")
            col.flush()

        await asyncio.to_thread(_delete)

    async def close(self) -> None:
        """幂等断开 Milvus 连接（shutdown 时调用；未连接则空操作）。"""
        if not self._connected:
            return
        try:
            await asyncio.to_thread(connections.disconnect, "default")
        finally:
            self._connected = False
