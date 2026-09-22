"""文档管理 API。"""
import uuid

import structlog
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_db,
    get_embed_client,
    get_es_client,
    get_milvus_client,
    get_redis,
    save_upload_to_staging,
    success_response,
)
from app.models.document import SummaryUpdateRequest
from app.parsers import ParserFactory
from app.repositories.asset_repo import AssetRepo
from app.repositories.chunk_repo import ChunkRepo
from app.repositories.doc_repo import DocRepo
from app.services import query_cache
from app.services.ingest_service import IngestService, _delete_assets_with_retry, schedule_background_ingest
from app.services.chunk_service import ChunkService
from app.services.rate_limit import rate_limiter
from app.services.require_role import require_roles
from app.services.summary_service import SummaryService
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from app.settings import settings
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
    redis=None,
) -> IngestService:
    return IngestService(
        doc_repo=DocRepo(db),
        chunk_repo=ChunkRepo(db),
        milvus_client=milvus,
        es_client=es,
        embed_client=embed,
        redis=redis,
    )


@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    category: str = Form(...),
    summary: str = Form(""),
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("kb_upload", 20)),
    db: AsyncSession = Depends(get_db),
):
    """上传文档异步入库（BREAKING：校验通过后立即返回 status=processing，后台完成索引）。

    - 校验失败仍同步返回 BizException；
    - 同名且 status=processing 的文档存在时拒绝重复上传（B0001）；
    - 客户端应轮询列表/详情直至 active 或 failed（可读 ingest_error）。
    """
    if category not in _VALID_CATEGORIES:
        raise BizException(ErrorCode.B0001, f"category 必须为: {_VALID_CATEGORIES}")
    filename = file.filename or ""
    if not ParserFactory.is_supported(filename):
        raise BizException(ErrorCode.A3001, f"不支持的文件格式: {filename}")
    if summary and len(summary) > settings.KB_SUMMARY_MAX_LEN:
        raise BizException(
            ErrorCode.B0001,
            f"summary 长度超过上限 {settings.KB_SUMMARY_MAX_LEN} 字符",
        )

    doc_repo = DocRepo(db)
    if await doc_repo.find_processing_by_title(filename):
        raise BizException(
            ErrorCode.B0001,
            "同名文件正在入库中，请稍后刷新列表查看进度",
        )
    if await doc_repo.find_active_by_title(filename):
        raise BizException(
            ErrorCode.B0001,
            "同名文件已在知识库中（active），请先删除后再上传",
        )

    try:
        doc_id = await doc_repo.create(
            title=filename,
            category=category,
            content="",
        )
        await doc_repo.update_ingest_stage(doc_id, "parse")
        file_path = await save_upload_to_staging(file, doc_id)
        await db.commit()

        schedule_background_ingest(
            doc_id, file_path, filename, category, summary or None
        )

        if settings.KB_CACHE_ENABLED:
            await query_cache.invalidate_category(category)

        return success_response({
            "doc_id": str(doc_id),
            "status": "processing",
        })
    except BizException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("upload_failed", error=str(e), exc_info=True)
        raise BizException(ErrorCode.B0007, "文档上传失败，请稍后重试")


@router.get("")
async def list_documents(
    category: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
    size: int | None = Query(None, ge=1, le=100),
    rate: None = Depends(rate_limiter("kb_list", 60)),
    db: AsyncSession = Depends(get_db),
):
    resolved_size = page_size or size or 20
    doc_repo = DocRepo(db)
    items, total = await doc_repo.list_by_category(
        category=category, status=status, page=page, size=resolved_size
    )
    return success_response({
        "items": items,
        "total": total,
        "page": page,
        "page_size": resolved_size,
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
    assets = await AssetRepo(db).get_by_doc(uid)
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
        "summary": doc.summary,
        "ingest_stage": doc.ingest_stage,
        "ingest_progress": doc.ingest_progress,
        "ingest_error": doc.ingest_error,
        "assets": [
            {
                "asset_type": a.asset_type,
                "page": a.page,
                "storage_path": a.storage_path,
                "mime_type": a.mime_type,
            }
            for a in assets
        ],
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
    embed = await get_embed_client()
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
    """重新索引（同步）：默认仅按 PG 正文重切，不重建多模态资产。"""
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

    await _delete_assets_with_retry(doc_id, doc_repo)

    await doc_repo.update_status(uid, "processing")

    chunk_service = ChunkService()
    cleaned_text = doc.content
    chunks = chunk_service.split(cleaned_text, doc.category)
    if not chunks:
        await doc_repo.update_status(uid, "failed")
        await doc_repo.mark_failed(uid, "文档重新分块后无有效内容")
        await db.commit()
        raise BizException(ErrorCode.A3002, "文档重新分块后无有效内容")

    import uuid as uuid_mod
    chunk_uuids = [uuid_mod.uuid4() for _ in chunks]
    for i, chunk in enumerate(chunks):
        chunk["id"] = str(chunk_uuids[i])
        chunk["doc_id"] = doc_id

    milvus_written = False
    try:
        texts = [c["content"] for c in chunks]
        vectors = await embed.batch_embed(texts)
        if texts and not vectors:
            raise RuntimeError("Embedding service returned empty vectors")
    except Exception as e:
        await doc_repo.mark_failed(uid, "向量入库失败")
        await db.commit()
        logger.error("reindex_embed_failed", doc_id=doc_id, error=str(e))
        raise BizException(ErrorCode.A3003, "向量入库失败")

    try:
        metadata = [
            {"chunk_id": c["id"], "doc_id": c["doc_id"], "category": c["category"]}
            for c in chunks
        ]
        vector_ids = await milvus.insert(vectors, metadata)
        milvus_written = True
    except Exception as e:
        await doc_repo.mark_failed(uid, "向量入库失败")
        await db.commit()
        logger.error("reindex_milvus_failed", doc_id=doc_id, error=str(e))
        raise BizException(ErrorCode.A3003, "向量入库失败")

    for i, c in enumerate(chunks):
        c["vector_id"] = vector_ids[i]

    try:
        es_docs = [
            {
                "chunk_id": c["id"],
                "content": c["content"],
                "doc_id": c["doc_id"],
                "category": c["category"],
                "doc_summary": doc.summary or "",
            }
            for c in chunks
        ]
        await es.index_docs(es_docs)
    except Exception as e:
        if milvus_written:
            try:
                await milvus.delete_by_doc(doc_id)
            except Exception as milvus_e:
                logger.warning("reindex_orphan_milvus_cleanup_failed", doc_id=doc_id, error=str(milvus_e))
        await doc_repo.mark_failed(uid, "ES 索引写入失败")
        await db.commit()
        logger.error("reindex_es_failed", doc_id=doc_id, error=str(e))
        raise BizException(ErrorCode.A3003, "ES 索引写入失败")

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
    await doc_repo.clear_ingest_progress(uid)

    await db.commit()
    if settings.KB_CACHE_ENABLED:
        await query_cache.invalidate_category(doc.category)
    return success_response({
        "doc_id": doc_id,
        "chunks_count": len(chunks),
        "status": "active",
        "assets_rebuilt": False,
    })


@router.put("/{doc_id}/summary")
async def update_document_summary(
    doc_id: str,
    req: SummaryUpdateRequest,
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("kb_summary", 10)),
    db: AsyncSession = Depends(get_db),
    es: ESClient = Depends(get_es_client),
):
    try:
        uid = uuid.UUID(doc_id)
    except ValueError:
        raise BizException(ErrorCode.B0001, "doc_id 格式错误")

    doc_repo = DocRepo(db)
    doc = await doc_repo.get_by_id(uid)
    if doc is None:
        raise BizException(ErrorCode.B0004, f"文档不存在: {doc_id}")

    summary_svc = SummaryService(doc_repo=doc_repo, es_client=es)
    try:
        await summary_svc.write_summary(
            uid, req.summary, retries=3, raise_on_failure=True
        )
        await db.commit()
    except BizException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("summary_update_failed", doc_id=doc_id, error=str(e))
        raise BizException(ErrorCode.B0007, f"摘要更新失败: {e}")

    return success_response({
        "doc_id": doc_id,
        "summary": req.summary,
    })
