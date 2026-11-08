"""文档管理 API。"""
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_db,
    get_embed_client,
    get_es_client,
    get_jwt_payload,
    get_milvus_client,
    get_redis,
    get_row_access_service,
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
from app.services.row_access_service import RowAccessService
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
    - 并发同名上传由部分唯一索引 uq_kb_documents_active_title 兜底（T119，迁移 0019）：
      冲突 rollback（无僵尸行）后自愈查询既有行，转 B0001 明确冲突；
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

    doc_id: uuid.UUID | None = None
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
    except IntegrityError as e:
        # T119（spec rag-service/document-ingest「文档写入竞态原子性」）：并发同名
        # 上传在 check-then-act 窗口外命中部分唯一索引 uq_kb_documents_active_title
        # （迁移 0019：title 唯一，限定 active/processing 且未删除）——rollback 撤销
        # 本事务未提交行（无僵尸行）；随后自愈查询既有 active/processing 行，将冲突
        # 转为明确 B0001（消息携带既有 doc_id，调用方据此查询收敛到唯一有效记录）；
        # 后台入库不会调度，staging 文件（若已落盘）best-effort 清理。
        # 仅唯一冲突（pgcode 23505）转 B0001，FK/CHECK 等其余 IntegrityError 保持
        # B0007 真实失败语义（对齐 complaint session_service T54 W1 收窄口径）。
        await db.rollback()
        if getattr(getattr(e, "orig", None), "pgcode", None) != "23505":
            logger.error("upload_integrity_error", error=str(e))
            raise BizException(ErrorCode.B0007, "文档上传失败，请稍后重试") from e
        existing = None
        try:
            existing = await doc_repo.find_processing_by_title(filename)
            if existing is None:
                existing = await doc_repo.find_active_by_title(filename)
        except Exception:
            existing = None
        if doc_id is not None:
            try:
                Path(settings.KB_INGEST_STAGING_DIR, f"{doc_id}{Path(filename).suffix}").unlink(missing_ok=True)
            except OSError:
                logger.warning("upload_conflict_staging_cleanup_failed", doc_id=str(doc_id))
        if existing is not None and existing.status == "active":
            raise BizException(
                ErrorCode.B0001,
                f"同名文件已在知识库中（active），请先删除后再上传（既有文档 id: {existing.id}）",
            ) from e
        if existing is not None:
            raise BizException(
                ErrorCode.B0001,
                f"同名文件正在入库中，请稍后刷新列表查看进度（既有文档 id: {existing.id}）",
            ) from e
        raise BizException(ErrorCode.B0001, "同名文件上传冲突，请稍后重试") from e
    except Exception as e:
        await db.rollback()
        logger.error("upload_failed", error=str(e), exc_info=True)
        raise BizException(ErrorCode.B0007, "文档上传失败，请稍后重试")


@router.get("")
async def list_documents(
    request: Request,
    category: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
    size: int | None = Query(None, ge=1, le=100),
    rate: None = Depends(rate_limiter("kb_list", 60)),
    row_access: RowAccessService = Depends(get_row_access_service),
    db: AsyncSession = Depends(get_db),
):
    """T92：list 接入行级权限判定（与 /search 同一判定入口）——匿名/受限
    调用方仅见白名单/矩阵可见类目；请求 category 不在可见集时按过滤形态
    返回空列表（与 /search 过滤语义一致，不取 403），响应条目不出现受限
    类目（无权限放大）。"""
    resolved_size = page_size or size or 20
    doc_repo = DocRepo(db)
    items, total = await doc_repo.list_by_category(
        category=category, status=status, page=page, size=resolved_size
    )
    jwt_payload = get_jwt_payload(request)
    filtered = row_access.filter(items, jwt_payload, category_filter=category)
    if jwt_payload is None:
        # T92 / spec 6.3.2-3：内部诊断字段 ingest_error 对匿名剥离（与详情端点同形态）
        filtered = [{k: v for k, v in item.items() if k != "ingest_error"} for item in filtered]
    return success_response({
        "items": filtered,
        "total": total,
        "page": page,
        "page_size": resolved_size,
    })


@router.get("/{doc_id}")
async def get_document(
    doc_id: str,
    request: Request,
    rate: None = Depends(rate_limiter("kb_detail", 60)),
    row_access: RowAccessService = Depends(get_row_access_service),
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

    # T92：与 /search 同一判定入口——调用方不可见类目的详情按 /search 过滤
    # 语义拒绝（404 B0004，不暴露受限文档存在性；形态在任务状态行留痕），
    # 堵住"匿名先列表取 id 再读受限类目全文"的权限放大链路
    jwt_payload = get_jwt_payload(request)
    if not row_access.filter([{"category": doc.category}], jwt_payload):
        raise BizException(ErrorCode.B0004, f"文档不存在: {doc_id}")

    chunks_count = await doc_repo.get_chunks_count(uid)
    assets = await AssetRepo(db).get_by_doc(uid)
    data = {
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
    }
    if jwt_payload is None:
        # T92 / spec 6.3.2-3：内部诊断字段 MUST NOT 对匿名返回（含 assets 内层
        # storage_path）；content 因匿名白名单类目公开语义保持（spec 6.3.1/6.3.4）
        data.pop("ingest_error", None)
        data["assets"] = [
            {k: v for k, v in asset.items() if k != "storage_path"}
            for asset in data["assets"]
        ]
    return success_response(data)


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
