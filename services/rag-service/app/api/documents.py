"""文档管理 API。"""
import uuid

import structlog
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_embed_client, get_es_client, get_milvus_client, save_upload_file, success_response
from app.models.document import SummaryUpdateRequest
from app.parsers import ParserFactory
from app.repositories.chunk_repo import ChunkRepo
from app.repositories.doc_repo import DocRepo
from app.services import query_cache
from app.services.ingest_service import IngestService
from app.services.chunk_service import ChunkService
from app.services.rate_limit import rate_limiter
from app.services.require_role import require_roles
from app.services.summary_service import SummaryService
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
    # V4 新增：可选摘要（上游业务中台预生成，随入库提交；不携带 = 存量行为完全不变）
    summary: str = Form(""),
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("kb_upload", 20)),
    db: AsyncSession = Depends(get_db),
    milvus: MilvusClient = Depends(get_milvus_client),
    es: ESClient = Depends(get_es_client),
    embed: EmbedClient = Depends(get_embed_client),
):
    """上传文档入库（V4 新增可选 form 字段 `summary`，其余为存量接口保持不变）。

    - 存量行为不变：`file` + `category` 必填，解析 → 清洗 → 分块 → 双索引入库；
    - **新增（V4）**：可选 form 字段 `summary`（文档级摘要，上游业务中台预生成），
      长度超 `KB_SUMMARY_MAX_LEN`（默认 200）返回 B0001；不携带 = 存量行为完全不变；
    - **新增（V4）**：携带摘要时按 ES（检索副本）→ PG（权威源）顺序写入，
      ES 写失败仅 warning（含 trace_id）+ PG 不写，正文已 active 不回滚、不返回错误；
    - 权限：ADMIN/SUPERVISOR；限流 20/min。
    """
    if category not in _VALID_CATEGORIES:
        raise BizException(ErrorCode.B0001, f"category 必须为: {_VALID_CATEGORIES}")
    if not ParserFactory.is_supported(file.filename or ""):
        raise BizException(ErrorCode.A3001, f"不支持的文件格式: {file.filename}")
    # V4 校验：摘要长度超上限返回 B0001（空串/未携带跳过，存量行为不变）
    if summary and len(summary) > settings.KB_SUMMARY_MAX_LEN:
        raise BizException(
            ErrorCode.B0001,
            f"summary 长度超过上限 {settings.KB_SUMMARY_MAX_LEN} 字符",
        )

    file_path = await save_upload_file(file)
    ingest = _get_ingest_service(db, milvus, es, embed)
    try:
        result = await ingest.ingest(
            file_path, file.filename, category, summary=summary or None
        )
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
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
    size: int | None = Query(None, ge=1, le=100),
    rate: None = Depends(rate_limiter("kb_list", 60)),
    db: AsyncSession = Depends(get_db),
):
    # 分页双支持（对齐 step5/02）：page_size 优先，size 兼容回退，默认 20，最大 100
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
    """查询文档详情（V4 新增响应字段 `summary`，其余为存量接口保持不变）。

    - 存量字段不变：id/title/category/content/status/version/chunks_count/create_time/update_time；
    - **新增（V4）**：响应新增 `summary`（str，可空，来源 PG kb_documents.summary 权威源，
      存量/无摘要文档为 null）；文档列表接口不新增该字段，保持负载不变；
    - doc_id 不存在/已归档 → B0004。
    """
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
        # V4 新增：文档级摘要（PG 权威源，可空；存量/无摘要文档为 null）
        "summary": doc.summary,
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


@router.put("/{doc_id}/summary")
async def update_document_summary(
    doc_id: str,
    req: SummaryUpdateRequest,
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("kb_summary", 10)),
    db: AsyncSession = Depends(get_db),
    es: ESClient = Depends(get_es_client),
):
    """V4 新增：补全/更新文档摘要（存量文档、多模态异步入库后补场景）。

    - 请求体 {"summary": str}，长度超 KB_SUMMARY_MAX_LEN（默认 200）由模型校验返回 B0001；
    - doc_id 不存在/已归档（is_delete='1'）→ B0004；
    - 写入顺序 ES（检索副本）→ PG（权威源），ES 写失败退避重试 3 次（1s/2s/4s），
      重试仍失败返回失败（复用 A3003 语义），PG 不写（无半状态）；
    - 全部成功失效该文档 category 的查询缓存。
    """
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
