"""Milvus V2 collection 重建脚本：从 kb_chunks 全量重嵌入到 kb_vectors_v2。

流程：
1. 读取 kb_chunks 全部有 vector_id 的记录
2. 调 EmbedClient.batch_embed 重新向量化
3. 在 kb_vectors_v2（新 schema 含 version/source）写入
4. 校验旧 collection.count == 新 collection.count（差异阈值内）
5. 若一致则切换 alias（通过 ensure_collection 自动检测 v2 优先）
6. 失败时 log 并保留旧 collection 不切换

使用方式：
    conda run -n ai_collection python scripts/rag/rebuild_milvus_v2.py

前置条件：Milvus / PostgreSQL / Embedding service 均已启动。
"""
import asyncio
import sys
import uuid

# 确保项目根目录在 sys.path
sys.path.insert(0, ".")

import structlog
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from sqlalchemy import select

from app.models.orm import KBChunk, KBDocument
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.milvus_client import MilvusClient, uuid_to_int64
from common.config.settings import settings
from common.db.session import async_session_factory
from redis.asyncio import Redis

logger = structlog.get_logger()

V2_COLLECTION = "kb_vectors_v2"
DIM = 1024
BATCH_SIZE = 100


async def create_v2_collection() -> Collection:
    """创建 kb_vectors_v2 collection（含 version/source 字段）。"""
    connections.connect(alias="default", host=settings.MILVUS_HOST, port=str(settings.MILVUS_PORT))

    if utility.has_collection(V2_COLLECTION):
        logger.info("v2_collection_already_exists", collection=V2_COLLECTION)
        return Collection(V2_COLLECTION)

    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.INT64, is_primary=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=DIM),
        FieldSchema(name="doc_id", dtype=DataType.INT64),
        FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=50),
        FieldSchema(name="chunk_id_str", dtype=DataType.VARCHAR, max_length=36),
        FieldSchema(name="doc_id_str", dtype=DataType.VARCHAR, max_length=36),
        FieldSchema(name="version", dtype=DataType.INT64),
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=255),
    ]
    schema = CollectionSchema(fields, "RAG knowledge base vectors V2 (with version/source)")
    col = Collection(V2_COLLECTION, schema)
    col.create_index(
        field_name="vector",
        index_params={
            "index_type": "IVF_FLAT",
            "metric_type": "IP",
            "params": {"nlist": 1024},
        },
    )
    logger.info("v2_collection_created", collection=V2_COLLECTION)
    return col


async def rebuild() -> None:
    """主重建流程。"""
    redis = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.RAG_REDIS_DB,
        decode_responses=True,
    )
    embed_client = EmbedClient(redis=redis)

    # 1. 创建 v2 collection
    v2_col = await create_v2_collection()

    # 2. 读取 kb_chunks + 文档元数据
    async with async_session_factory() as db:
        result = await db.execute(
            select(KBChunk, KBDocument)
            .join(KBDocument, KBChunk.doc_id == KBDocument.id)
            .where(KBChunk.is_delete == "0", KBDocument.is_delete == "0")
        )
        rows = result.all()

    logger.info("chunks_to_rebuild", count=len(rows))
    if not rows:
        logger.warning("no_chunks_found_aborting")
        return

    # 3. 分批重嵌入 + 写入 v2
    total_inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        contents = [chunk.content for chunk, _ in batch]

        # 重新向量化
        vectors = await embed_client.batch_embed(contents)

        # 构造写入数据
        chunk_ids = [uuid_to_int64(str(chunk.id)) for chunk, _ in batch]
        doc_ids = [uuid_to_int64(str(chunk.doc_id)) for chunk, _ in batch]
        categories = [chunk.category or "other" for chunk, _ in batch]
        chunk_id_strs = [str(chunk.id) for chunk, _ in batch]
        doc_id_strs = [str(chunk.doc_id) for chunk, _ in batch]
        versions = [doc.version or 1 for _, doc in batch]
        sources = [doc.source or "v1_legacy" for _, doc in batch]

        v2_col.insert([
            chunk_ids,
            vectors,
            doc_ids,
            categories,
            chunk_id_strs,
            doc_id_strs,
            versions,
            sources,
        ])
        total_inserted += len(batch)
        logger.info("batch_inserted", batch=i // BATCH_SIZE + 1, total=total_inserted)

    v2_col.flush()

    # 4. 校验 count
    old_col = Collection(MilvusClient.COLLECTION_NAME) if utility.has_collection(MilvusClient.COLLECTION_NAME) else None
    old_count = old_col.num_entities if old_col else 0
    new_count = v2_col.num_entities
    diff = abs(old_count - new_count)
    threshold = max(1, int(old_count * 0.01))  # 1% 差异阈值

    logger.info("count_comparison", old=old_count, new=new_count, diff=diff, threshold=threshold)

    if diff > threshold:
        logger.error(
            "count_mismatch_exceeds_threshold_aborting",
            old=old_count,
            new=new_count,
            diff=diff,
        )
        # 不切换 alias，保留旧 collection
        return

    # 5. 切换完成（ensure_collection 会自动检测 v2 优先使用）
    logger.info(
        "rebuild_complete_v2_active",
        collection=V2_COLLECTION,
        old_count=old_count,
        new_count=new_count,
    )

    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(rebuild())
