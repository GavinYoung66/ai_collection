"""文档入库编排：解析 → 清洗 → 分块 → 向量化 → 双索引写入。"""
import uuid
from pathlib import Path

import structlog

from app.parsers import ParserFactory
from app.parsers.audio_parser import AudioParser
from app.parsers.image_parser import ImageParser
from app.repositories.chunk_repo import ChunkRepo
from app.repositories.doc_repo import DocRepo
from app.services.chunk_service import ChunkService
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

logger = structlog.get_logger()


def _detect_source(parser) -> str:
    """根据 Parser 实例类型推断文档来源标签。

    - ImageParser → "ocr"
    - AudioParser → "asr"
    - 其他（Text/MD/PDF/Docx）→ "v1_legacy"
    """
    if isinstance(parser, ImageParser):
        return "ocr"
    if isinstance(parser, AudioParser):
        return "asr"
    return "v1_legacy"


class IngestService:
    def __init__(
        self,
        doc_repo: DocRepo,
        chunk_repo: ChunkRepo,
        milvus_client: MilvusClient,
        es_client: ESClient,
        embed_client: EmbedClient,
        chunk_service: ChunkService | None = None,
    ):
        self.doc_repo = doc_repo
        self.chunk_repo = chunk_repo
        self.milvus = milvus_client
        self.es = es_client
        self.embed = embed_client
        self.chunk_service = chunk_service or ChunkService()

    async def ingest(
        self, file_path: Path, filename: str, category: str,
        created_by: uuid.UUID | None = None,
    ) -> dict:
        if not ParserFactory.is_supported(filename):
            raise BizException(ErrorCode.A3001, f"不支持的文件格式: {Path(filename).suffix}")

        try:
            parser = ParserFactory.get_parser(file_path)
            raw_text = await parser.extract_text(file_path)
        except BizException:
            raise
        except Exception as e:
            logger.error("parse_failed", filename=filename, error=str(e))
            raise BizException(ErrorCode.A3001, f"文档解析失败: {e}")

        # V2: 根据 Parser 类型记录文档来源标签（§6.6 / §15.1）
        source = _detect_source(parser)

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

        await self.doc_repo.update_status(doc_id, "active")

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

    async def delete_by_doc(self, doc_id: str) -> None:
        uid = uuid.UUID(doc_id)
        await self.chunk_repo.delete_by_doc(uid)
        await self.milvus.delete_by_doc(doc_id)
        await self.es.delete_by_doc(doc_id)
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

        await self.doc_repo.update_status(uid, "processing")

        return await self.ingest(file_path, filename, category)
