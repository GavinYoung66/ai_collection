"""文档管理 API。"""
import uuid

import structlog
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_embed_client, get_es_client, get_milvus_client, save_upload_file, success_response
from app.models.document import DocumentDetailResponse, DocumentListResponse, DocumentUploadResponse
from app.parsers import ParserFactory
from app.repositories.chunk_repo import ChunkRepo
from app.repositories.doc_repo import DocRepo
from app.services import query_cache
from app.services.ingest_service import IngestService
from app.services.chunk_service import ChunkService
from app.services.rate_limit import rate_limiter
from app.services.require_role import require_roles
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

logger = structlog.get_logger()
router = APIRouter(tags=["文档管理"])

_VALID_CATEGORIES = {"regulation", "script", "case", "other"}


def _get_ingest_service(
    db: AsyncSession,
    milvus: MilvusClient,
    es: ESClient,
    embed: EmbedClient,
) -> IngestService:
    return IngestService(
        doc_repo=DocRepo(db),
        chunk_repo=ChunkRepo(db),
        milvus_client=milvus,
        es_client=es,
        embed_client=embed,
    )


@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    category: str = Form(...),
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("kb_upload", 20)),
    db: AsyncSession = Depends(get_db),
    milvus: MilvusClient = Depends(get_milvus_client),
    es: ESClient = Depends(get_es_client),
    embed: EmbedClient = Depends(get_embed_client),
):
    if category not in _VALID_CATEGORIES:
        raise BizException(ErrorCode.B0001, f"category 必须为: {_VALID_CATEGORIES}")
    if not ParserFactory.is_supported(file.filename or ""):
        raise BizException(ErrorCode.A3001, f"不支持的文件格式: {file.filename}")

    file_path = await save_upload_file(file)
    ingest = _get_ingest_service(db, milvus, es, embed)
    try:
        result = await ingest.ingest(file_path, file.filename, category)
        await db.commit()
        if settings.KB_CACHE_ENABLED:
            await query_cache.invalidate_category(category)
        return success_response(result)
    except BizException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("upload_failed", error=str(e))
        raise BizException(ErrorCode.B0007, f"文档上传失败: {e}")


@router.get("")
async def list_documents(
    category: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    rate: None = Depends(rate_limiter("kb_list", 60)),
    db: AsyncSession = Depends(get_db),
):
    doc_repo = DocRepo(db)
    items, total = await doc_repo.list_by_category(
        category=category, status=status, page=page, size=size
    )
    return success_response({
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    })


@router.get("/{doc_id}")
async def get_document(
    doc_id: str,
    rate: None = Depends(rate_limiter("kb_detail", 60)),
    db: AsyncSession = Depends(get_db),
):
    try:
        uid = uuid.UUID(doc_id)
    except ValueError:
        raise BizException(ErrorCode.B0001, "doc_id 格式错误")

    doc_repo = DocRepo(db)
    doc = await doc_repo.get_by_id(uid)
    if doc is None:
        raise BizException(ErrorCode.B0004, f"文档不存在: {doc_id}")

    chunks_count = await doc_repo.get_chunks_count(uid)
    return success_response({
        "id": str(doc.id),
        "title": doc.title,
        "category": doc.category,
        "content": doc.content,
        "status": doc.status,
        "version": doc.version,
        "chunks_count": chunks_count,
        "create_time": doc.create_time.isoformat() if doc.create_time else "",
        "update_time": doc.update_time.isoformat() if doc.update_time else "",
    })


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("kb_delete", 20)),
    db: AsyncSession = Depends(get_db),
):
    try:
        uid = uuid.UUID(doc_id)
    except ValueError:
        raise BizException(ErrorCode.B0001, "doc_id 格式错误")

    doc_repo = DocRepo(db)
    doc = await doc_repo.get_by_id(uid)
    if doc is None:
        raise BizException(ErrorCode.B0004, f"文档不存在: {doc_id}")

    milvus = get_milvus_client()
    es = get_es_client()
    embed = get_embed_client()
    ingest = _get_ingest_service(db, milvus, es, embed)
    try:
        await ingest.delete_by_doc(doc_id)
        await db.commit()
        if settings.KB_CACHE_ENABLED:
            await query_cache.invalidate_category(doc.category)
        return success_response(None, "文档已删除")
    except Exception as e:
        await db.rollback()
        logger.error("delete_failed", doc_id=doc_id, error=str(e))
        raise BizException(ErrorCode.B0007, f"文档删除失败: {e}")


@router.post("/{doc_id}/reindex")
async def reindex_document(
    doc_id: str,
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("kb_reindex", 10)),
    db: AsyncSession = Depends(get_db),
    milvus: MilvusClient = Depends(get_milvus_client),
    es: ESClient = Depends(get_es_client),
    embed: EmbedClient = Depends(get_embed_client),
):
    try:
        uid = uuid.UUID(doc_id)
    except ValueError:
        raise BizException(ErrorCode.B0001, "doc_id 格式错误")

    doc_repo = DocRepo(db)
    doc = await doc_repo.get_by_id(uid)
    if doc is None:
        raise BizException(ErrorCode.B0004, f"文档不存在: {doc_id}")

    chunk_repo = ChunkRepo(db)
    await chunk_repo.delete_by_doc(uid)
    try:
        await milvus.delete_by_doc(doc_id)
    except Exception as e:
        logger.warning("milvus_delete_failed", doc_id=doc_id, error=str(e))
    try:
        await es.delete_by_doc(doc_id)
    except Exception as e:
        logger.warning("es_delete_failed", doc_id=doc_id, error=str(e))

    await doc_repo.update_status(uid, "processing")

    chunk_service = ChunkService()
    cleaned_text = doc.content
    chunks = chunk_service.split(cleaned_text, doc.category)
    if not chunks:
        await doc_repo.update_status(uid, "failed")
        await db.rollback()
        raise BizException(ErrorCode.A3002, "文档重新分块后无有效内容")

    import uuid as uuid_mod
    chunk_uuids = [uuid_mod.uuid4() for _ in chunks]
    for i, chunk in enumerate(chunks):
        chunk["id"] = str(chunk_uuids[i])
        chunk["doc_id"] = doc_id

    try:
        texts = [c["content"] for c in chunks]
        vectors = await embed.batch_embed(texts)
    except Exception as e:
        await doc_repo.update_status(uid, "failed")
        await db.rollback()
        raise BizException(ErrorCode.A3003, f"向量入库失败: {e}")

    try:
        metadata = [
            {"chunk_id": c["id"], "doc_id": c["doc_id"], "category": c["category"]}
            for c in chunks
        ]
        vector_ids = await milvus.insert(vectors, metadata)
    except Exception as e:
        await doc_repo.update_status(uid, "failed")
        await db.rollback()
        raise BizException(ErrorCode.A3003, f"向量入库失败: {e}")

    for i, c in enumerate(chunks):
        c["vector_id"] = vector_ids[i]

    try:
        es_docs = [
            {
                "chunk_id": c["id"],
                "content": c["content"],
                "doc_id": c["doc_id"],
                "category": c["category"],
            }
            for c in chunks
        ]
        await es.index_docs(es_docs)
    except Exception as e:
        await doc_repo.update_status(uid, "failed")
        await db.rollback()
        raise BizException(ErrorCode.A3003, f"ES 索引写入失败: {e}")

    chunk_entities = [
        {
            "id": uuid_mod.UUID(c["id"]),
            "doc_id": uid,
            "chunk_index": c["chunk_index"],
            "content": c["content"],
            "vector_id": c["vector_id"],
            "category": c["category"],
        }
        for c in chunks
    ]
    await chunk_repo.batch_create(chunk_entities)
    await doc_repo.update_status(uid, "active")

    await db.commit()
    if settings.KB_CACHE_ENABLED:
        await query_cache.invalidate_category(doc.category)
    return success_response({
        "doc_id": doc_id,
        "chunks_count": len(chunks),
        "status": "active",
    })
