"""文档入库编排：解析 → 清洗 → 分块 → 向量化 → 双索引写入。"""
import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.parsers import ParserFactory
from app.parsers.asset_extractor import assets_enabled
from app.parsers.audio_parser import AudioParser
from app.parsers.image_parser import ImageParser
from app.parsers.pptx_parser import PptxParser
from app.parsers.xlsx_parser import XlsxParser
from app.repositories.chunk_repo import ChunkRepo
from app.repositories.doc_repo import DocRepo
from app.services.asset_service import AssetService, remove_assets_dir
from app.services.chunk_service import ChunkService
from app.services.summary_service import SummaryService
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

logger = structlog.get_logger()

# ES 降级补索引的待补集合（rag-service-final-fix 5.9：重启后由 main.py startup 扫描恢复）
_BACKFILL_PENDING_KEY = "rag:backfill:pending"
_BACKFILL_PENDING_TTL = 30 * 60


class ParseTimeoutError(Exception):
    """文档解析超时（KB_PARSE_TIMEOUT，默认 30s），按既有解析失败路径处理。"""


def _detect_source(parser) -> str:
    """根据 Parser 实例类型推断文档来源标签。

    - ImageParser → "ocr"
    - AudioParser → "asr"（含 V5 新增 AMR/WMA，与 MP3/WAV/M4A 一致）
    - XlsxParser → "excel"
    - PptxParser → "ppt"
    - 其他（Text/MD/PDF/Docx）→ "v1_legacy"
    """
    if isinstance(parser, ImageParser):
        return "ocr"
    if isinstance(parser, AudioParser):
        return "asr"
    if isinstance(parser, XlsxParser):
        return "excel"
    if isinstance(parser, PptxParser):
        return "ppt"
    return "v1_legacy"


def _resolve_ingest_category(category: str, filename: str, raw_text: str) -> str:
    """P0：合规类 PDF 若误选 other，自动提升为 regulation 便于检索与 LLM 合规策略。"""
    if category != "other":
        return category
    name = filename.lower()
    if not name.endswith(".pdf"):
        return category
    sample = raw_text[:4000]
    markers = ("合规", "催收", "流程图", "话术", "监管", "自律", "风控")
    hits = sum(1 for m in markers if m in sample)
    if hits >= 2:
        logger.info(
            "ingest_category_promoted",
            filename=filename,
            from_category="other",
            to_category="regulation",
            marker_hits=hits,
        )
        return "regulation"
    return category


async def _delete_assets_with_retry(doc_id: str, doc_repo: DocRepo) -> None:
    """V5：删除文档/reindex 时级联清理 kb_doc_assets 记录 + 本地资产目录。

    失败按既有"删除失败重试与补偿"契约：重试 2 次（1s/3s）+ 告警标记，不阻断主流程
    （PG 软删除与业务返回不受影响，后续 reindex 兜底）。
    """
    delays = (1.0, 3.0)
    for attempt in range(3):
        try:
            uid = uuid.UUID(doc_id)
            await doc_repo.delete_assets_by_doc(uid)
            remove_assets_dir(uid)
            return
        except Exception as e:
            if attempt < len(delays):
                logger.warning(
                    "asset_delete_retry_failed",
                    doc_id=doc_id,
                    attempt=attempt + 1,
                    error=str(e),
                )
                await asyncio.sleep(delays[attempt])
            else:
                logger.error(
                    "asset_delete_give_up",
                    doc_id=doc_id,
                    error=str(e),
                )


class IngestService:
    def __init__(
        self,
        doc_repo: DocRepo,
        chunk_repo: ChunkRepo,
        milvus_client: MilvusClient,
        es_client: ESClient,
        embed_client: EmbedClient,
        chunk_service: ChunkService | None = None,
        redis: Redis | None = None,
        asset_service: AssetService | None = None,
    ):
        self.doc_repo = doc_repo
        self.chunk_repo = chunk_repo
        self.milvus = milvus_client
        self.es = es_client
        self.embed = embed_client
        self.chunk_service = chunk_service or ChunkService()
        # V5 增量：补索引持久化（可选，缺省 None = 不持久化，存量行为不变）
        self._redis = redis
        # V5 增量：资产落库编排（可选，缺省 None = 惰性构建，不影响存量构造调用方）
        self.asset_service = asset_service

    def _get_asset_service(self) -> AssetService:
        if self.asset_service is None:
            from app.repositories.asset_repo import AssetRepo

            self.asset_service = AssetService(
                doc_repo=self.doc_repo,
                chunk_repo=self.chunk_repo,
                asset_repo=AssetRepo(self.doc_repo.db),
                milvus_client=self.milvus,
                es_client=self.es,
                embed_client=self.embed,
                redis=self._redis,
            )
        return self.asset_service

    async def ingest(
        self, file_path: Path, filename: str, category: str,
        created_by: uuid.UUID | None = None,
        summary: str | None = None,
    ) -> dict:
        if not ParserFactory.is_supported(filename):
            raise BizException(ErrorCode.A3001, f"不支持的文件格式: {Path(filename).suffix}")

        assets: list = []
        try:
            parser = ParserFactory.get_parser(file_path)
            parse_start = time.time()
            logger.info(
                "ingest_parse_start",
                filename=filename,
                timeout=settings.KB_PARSE_TIMEOUT,
                parse_start_ts=parse_start,
            )
            try:
                if assets_enabled():
                    # V5：内嵌图片/表格抽取开关开启 → 解析正文 + 资产（单次解析）
                    raw_text, assets = await asyncio.wait_for(
                        parser.extract_with_assets(file_path), timeout=settings.KB_PARSE_TIMEOUT
                    )
                else:
                    # 开关全关 → V4 原路径，零额外开销
                    raw_text = await asyncio.wait_for(
                        parser.extract_text(file_path), timeout=settings.KB_PARSE_TIMEOUT
                    )
            except asyncio.TimeoutError as te:
                logger.info(
                    "ingest_parse_timeout",
                    filename=filename,
                    timeout=settings.KB_PARSE_TIMEOUT,
                    elapsed_s=round(time.time() - parse_start, 3),
                )
                raise ParseTimeoutError(
                    f"解析超时（>{settings.KB_PARSE_TIMEOUT}s）"
                ) from te
            logger.info(
                "ingest_parse_done",
                filename=filename,
                elapsed_s=round(time.time() - parse_start, 3),
                text_len=len(raw_text or ""),
            )
        except BizException:
            raise
        except Exception as e:
            logger.error("parse_failed", filename=filename, error=str(e))
            if settings.KB_OCR_ASR_DEGRADED_KEEP_TEXT:
                # 降级：上传接口未附带纯文本，按「占位入库」处理——status=failed + degraded=true
                doc_id = await self.doc_repo.create(
                    title=filename, category=category, content="", created_by=created_by
                )
                await self.doc_repo.update_status(doc_id, "failed")
                logger.warning(
                    "ingest_degraded_parse_failed",
                    filename=filename,
                    doc_id=str(doc_id),
                    error=str(e),
                )
                return {
                    "doc_id": str(doc_id),
                    "chunks_count": 0,
                    "status": "failed",
                    "degraded": True,
                }
            raise BizException(ErrorCode.A3001, f"文档解析失败: {e}")

        # V2: 根据 Parser 类型记录文档来源标签（§6.6 / §15.1）
        source = _detect_source(parser)
        category = _resolve_ingest_category(category, filename, raw_text or "")

        cleaned_text = self.chunk_service.clean(raw_text)

        doc_id = await self.doc_repo.create(
            title=filename,
            category=category,
            content=cleaned_text,
            created_by=created_by,
            source=source,
        )

        try:
            chunks = self.chunk_service.split(cleaned_text, category)
            if not chunks:
                raise BizException(ErrorCode.A3002, "文档分块后无有效内容")
        except BizException:
            await self.doc_repo.update_status(doc_id, "failed")
            raise
        except Exception as e:
            logger.error("chunk_failed", doc_id=str(doc_id), error=str(e))
            await self.doc_repo.update_status(doc_id, "failed")
            raise BizException(ErrorCode.A3002, f"文本切片失败: {e}")

        chunk_uuids = [uuid.uuid4() for _ in chunks]
        for i, chunk in enumerate(chunks):
            chunk["id"] = str(chunk_uuids[i])
            chunk["doc_id"] = str(doc_id)

        try:
            texts = [c["content"] for c in chunks]
            vectors = await self.embed.batch_embed(texts)
        except Exception as e:
            logger.error("embed_failed", doc_id=str(doc_id), error=str(e))
            # V2 §6.6: 降级入库开关——Embedding 失败时仅写 ES + 文本，不写 Milvus/kb_chunks.vector_id=null
            if settings.KB_EMBED_DEGRADED_KEEP_TEXT:
                await self._ingest_degraded_fallback(doc_id, chunks, source, error=str(e))
                return {
                    "doc_id": str(doc_id),
                    "chunks_count": len(chunks),
                    "status": "active",
                    "degraded": True,
                }
            await self.doc_repo.update_status(doc_id, "failed")
            raise BizException(ErrorCode.A3003, f"向量入库失败: {e}")

        try:
            metadata = [
                {"chunk_id": c["id"], "doc_id": c["doc_id"], "category": c["category"]}
                for c in chunks
            ]
            vector_ids = await self.milvus.insert(vectors, metadata)
        except Exception as e:
            logger.error("milvus_insert_failed", doc_id=str(doc_id), error=str(e))
            await self.doc_repo.update_status(doc_id, "failed")
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
            await self.es.index_docs(es_docs)
        except Exception as e:
            logger.error("es_index_failed", doc_id=str(doc_id), error=str(e))
            if settings.KB_ES_DEGRADED_KEEP_MILVUS:
                # 降级：保留 Milvus + kb_chunks，status=active，异步补索引（指数退避 1s/2s/4s…，30min 窗口）
                chunk_entities = [
                    {
                        "id": uuid.UUID(c["id"]),
                        "doc_id": doc_id,
                        "chunk_index": c["chunk_index"],
                        "content": c["content"],
                        "vector_id": c["vector_id"],
                        "category": c["category"],
                    }
                    for c in chunks
                ]
                await self.chunk_repo.batch_create(chunk_entities)
                await self.doc_repo.update_status(doc_id, "active")
                logger.warning(
                    "ingest_degraded_es_fallback",
                    doc_id=str(doc_id),
                    chunks=len(chunks),
                    error=str(e),
                )
                asyncio.create_task(self._backfill_es_index(es_docs, doc_id))
                return {
                    "doc_id": str(doc_id),
                    "chunks_count": len(chunks),
                    "status": "active",
                    "degraded": True,
                }
            await self.doc_repo.update_status(doc_id, "failed")
            raise BizException(ErrorCode.A3003, f"ES 索引写入失败: {e}")

        chunk_entities = [
            {
                "id": uuid.UUID(c["id"]),
                "doc_id": doc_id,
                "chunk_index": c["chunk_index"],
                "content": c["content"],
                "vector_id": c["vector_id"],
                "category": c["category"],
            }
            for c in chunks
        ]
        await self.chunk_repo.batch_create(chunk_entities)

        # V5: 正文双索引完成后落内嵌资产（kb_doc_assets + 本地文件 + OCR/表格 chunk）；
        # 开关关闭时 assets 恒为空（短路零开销）；资产失败仅 warning 不回滚正文
        if assets:
            try:
                await self._get_asset_service().process_assets(doc_id, assets, category)
            except Exception as e:
                try:
                    await self.doc_repo.db.rollback()
                except Exception:
                    pass
                logger.warning("assets_process_failed", doc_id=str(doc_id), error=str(e))

        await self.doc_repo.update_status(doc_id, "active")

        # V4: 正文入库成功后，若携带摘要则按 ES → PG 顺序写入（design.md D3）；
        # ES 写失败仅 warning（含 trace_id）+ PG summary 不写，正文已 active 不回滚、不返回错误
        if summary:
            await SummaryService(self.doc_repo, self.es).write_summary(
                doc_id, summary, retries=0, raise_on_failure=False
            )

        return {
            "doc_id": str(doc_id),
            "chunks_count": len(chunks),
            "status": "active",
        }

    async def _ingest_degraded_fallback(
        self, doc_id: uuid.UUID, chunks: list[dict], source: str, error: str
    ) -> None:
        """V2 §6.6 降级入库：Embedding 失败但 KB_EMBED_DEGRADED_KEEP_TEXT=true 时，
        仅写 ES + kb_chunks（vector_id 占位为空串），文档 status=active，不写 Milvus。

        - 保证文本可被 ES BM25 检索到，向量召回路径静默缺失；
        - 日志 warning degraded，便于后续重建脚本（rebuild_milvus_v2.py）补齐向量。
        """
        logger.warning(
            "ingest_degraded_embed_fallback",
            doc_id=str(doc_id),
            source=source,
            error=error,
        )
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
            await self.es.index_docs(es_docs)
        except Exception as e:
            logger.error("degraded_es_index_failed", doc_id=str(doc_id), error=str(e))
            await self.doc_repo.update_status(doc_id, "failed")
            raise BizException(ErrorCode.A3003, f"降级入库 ES 写入失败: {e}")

        chunk_entities = [
            {
                "id": uuid.UUID(c["id"]),
                "doc_id": doc_id,
                "chunk_index": c["chunk_index"],
                "content": c["content"],
                # vector_id 唯一索引非空约束：降级时用占位 id 标记无向量
                "vector_id": f"degraded:{c['id']}",
                "category": c["category"],
            }
            for c in chunks
        ]
        await self.chunk_repo.batch_create(chunk_entities)
        await self.doc_repo.update_status(doc_id, "active")

    async def _backfill_es_index(self, es_docs: list[dict], doc_id: uuid.UUID) -> None:
        """兼容入口：委托模块级 backfill_es_index_task（5.9 补索引持久化逻辑统一）。"""
        return await backfill_es_index_task(self.es, self._redis, es_docs, doc_id)

    async def _delete_with_retry(self, op, doc_id: str, target: str) -> None:
        """删除外部索引失败重试 2 次（退避 1s/3s），仍失败记录告警日志。

        PG 软删除与业务返回不受影响（降级路径由后续 reindex 兜底）。
        """
        delays = (1.0, 3.0)
        for attempt in range(3):
            try:
                await op(doc_id)
                return
            except Exception as e:
                if attempt < len(delays):
                    logger.warning(
                        "delete_retry_failed",
                        target=target,
                        doc_id=doc_id,
                        attempt=attempt + 1,
                        error=str(e),
                    )
                    await asyncio.sleep(delays[attempt])
                else:
                    logger.error(
                        "delete_retry_give_up",
                        target=target,
                        doc_id=doc_id,
                        error=str(e),
                    )

    async def delete_by_doc(self, doc_id: str) -> None:
        uid = uuid.UUID(doc_id)
        await self.chunk_repo.delete_by_doc(uid)
        await self._delete_with_retry(self.milvus.delete_by_doc, doc_id, "milvus")
        await self._delete_with_retry(self.es.delete_by_doc, doc_id, "es")
        # V5: 级联清理 kb_doc_assets 记录 + 本地资产目录（重试 2 次 + 告警，不阻断）
        await _delete_assets_with_retry(doc_id, self.doc_repo)
        await self.doc_repo.soft_delete(uid)

    async def reindex(
        self, doc_id: str, file_path: Path, filename: str, category: str
    ) -> dict:
        uid = uuid.UUID(doc_id)
        doc = await self.doc_repo.get_by_id(uid)
        if doc is None:
            raise BizException(ErrorCode.B0004, f"文档不存在: {doc_id}")

        await self.chunk_repo.delete_by_doc(uid)
        try:
            await self.milvus.delete_by_doc(doc_id)
        except Exception as e:
            logger.warning("milvus_delete_old_failed", doc_id=doc_id, error=str(e))
        try:
            await self.es.delete_by_doc(doc_id)
        except Exception as e:
            logger.warning("es_delete_old_failed", doc_id=doc_id, error=str(e))
        # V5: reindex 前级联清理旧资产（记录 + 本地目录，重试 2 次 + 告警，不阻断）
        await _delete_assets_with_retry(doc_id, self.doc_repo)

        await self.doc_repo.update_status(uid, "processing")

        # V4：从 PG 回读 doc.summary，随 ingest 入库路径保持 ES doc_summary 与 PG summary 两端一致
        return await self.ingest(
            file_path, filename, category, summary=doc.summary or None
        )


async def backfill_es_index_task(
    es_client: ESClient,
    redis: Redis | None,
    es_docs: list[dict],
    doc_id: uuid.UUID,
) -> None:
    """ES 降级后的异步补索引：指数退避 1s/2s/4s…，30min 窗口内重试（模块级，main.py 启动恢复复用）。

    - 待补 doc_id 持久化到 Redis 集合 `rag:backfill:pending`（TTL 30min），
      进程重启后由 main.py startup 扫描恢复并重新调度（rag-service-final-fix 5.9）；
    - 成功或放弃后从集合移除；Redis 不可用仅 warning，不阻断补索引主流程；
    - 超窗或持续失败仅日志，由后续 reindex 兜底（document-ingest spec）。
    """
    if redis is not None:
        try:
            # redis.asyncio 方法桩返回 Awaitable|T 联合类型，cast 消除 union（与 query_cache 同因）
            await cast(Any, redis).sadd(_BACKFILL_PENDING_KEY, str(doc_id))
            await cast(Any, redis).expire(_BACKFILL_PENDING_KEY, _BACKFILL_PENDING_TTL)
        except (RedisError, OSError) as e:
            logger.warning(
                "backfill_pending_persist_failed",
                doc_id=str(doc_id),
                error=str(e),
            )
    delay = 1.0
    deadline = time.monotonic() + 30 * 60
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            try:
                await es_client.index_docs(es_docs)
                logger.info("es_backfill_success", doc_id=str(doc_id))
                return
            except Exception as e:
                logger.warning(
                    "es_backfill_retry_failed",
                    doc_id=str(doc_id),
                    delay=delay,
                    error=str(e),
                )
            delay = min(delay * 2, 60.0)
        logger.error("es_backfill_give_up", doc_id=str(doc_id))
    finally:
        if redis is not None:
            try:
                await cast(Any, redis).srem(_BACKFILL_PENDING_KEY, str(doc_id))
            except (RedisError, OSError) as e:
                logger.warning(
                    "backfill_pending_remove_failed",
                    doc_id=str(doc_id),
                    error=str(e),
                )
