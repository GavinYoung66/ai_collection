"""kb_documents 数据访问层。"""
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import KBChunk, KBDocument, KbDocAsset


class DocRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        title: str,
        category: str,
        content: str,
        created_by: uuid.UUID | None = None,
        source: str | None = None,
    ) -> uuid.UUID:
        # V2: source 记录文档来源（"ocr"/"asr"/"v1_legacy"/null），
        # 存量 V1 文档未传 source 时为 NULL，由 get_meta_dict 回填 "v1_legacy"。
        doc = KBDocument(
            title=title,
            category=category,
            content=content,
            status="processing",
            created_by=created_by,
            source=source,
        )
        self.db.add(doc)
        await self.db.flush()
        return doc.id

    async def get_by_id(self, doc_id: uuid.UUID) -> KBDocument | None:
        result = await self.db.execute(
            select(KBDocument).where(
                KBDocument.id == doc_id, KBDocument.is_delete == "0"
            )
        )
        return result.scalar_one_or_none()

    async def get_chunks_count(self, doc_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(KBChunk)
            .where(KBChunk.doc_id == doc_id, KBChunk.is_delete == "0")
        )
        return result.scalar() or 0

    async def list_by_category(
        self, category: str | None = None, status: str | None = None,
        page: int = 1, size: int = 20,
    ) -> tuple[list[dict], int]:
        stmt = select(KBDocument).where(KBDocument.is_delete == "0")
        if category:
            stmt = stmt.where(KBDocument.category == category)
        if status:
            stmt = stmt.where(KBDocument.status == status)
        stmt = stmt.order_by(KBDocument.create_time.desc())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await self.db.execute(stmt)
        docs = result.scalars().all()

        # 批量分组聚合 chunk 计数，消除逐行 get_chunks_count 的 N+1 查询（rag-service-final-fix 5.11）
        if docs:
            count_result = await self.db.execute(
                select(KBChunk.doc_id, func.count())
                .where(
                    KBChunk.doc_id.in_([d.id for d in docs]),
                    KBChunk.is_delete == "0",
                )
                .group_by(KBChunk.doc_id)
            )
            counts = {str(row[0]): row[1] for row in count_result.all()}
        else:
            counts = {}

        items = []
        for doc in docs:
            items.append({
                "id": str(doc.id),
                "title": doc.title,
                "category": doc.category,
                "status": doc.status,
                "chunks_count": counts.get(str(doc.id), 0),
                "create_time": doc.create_time.isoformat() if doc.create_time else "",
            })
        return items, total

    async def update_status(self, doc_id: uuid.UUID, status: str) -> None:
        await self.db.execute(
            update(KBDocument)
            .where(KBDocument.id == doc_id)
            .values(status=status)
        )

    async def soft_delete(self, doc_id: uuid.UUID) -> None:
        await self.db.execute(
            update(KBDocument)
            .where(KBDocument.id == doc_id)
            .values(is_delete="1", status="archived")
        )

    async def get_titles_map(self, doc_ids: list[uuid.UUID]) -> dict[str, str]:
        if not doc_ids:
            return {}
        result = await self.db.execute(
            select(KBDocument.id, KBDocument.title).where(
                KBDocument.id.in_(doc_ids), KBDocument.is_delete == "0"
            )
        )
        return {str(row[0]): row[1] for row in result.all()}

    async def get_meta_dict(self, doc_ids: list[uuid.UUID]) -> dict[str, dict]:
        """批量查询文档 V2 元数据，返回 {doc_id_str: {customer_id, agent_id, call_date, risk_tag, source, version}}。

        存量 V1 文档（source/version 为 NULL）由调用方回填默认值 source='v1_legacy', version=1。
        """
        if not doc_ids:
            return {}
        result = await self.db.execute(
            select(
                KBDocument.id,
                KBDocument.customer_id,
                KBDocument.agent_id,
                KBDocument.call_date,
                KBDocument.risk_tag,
                KBDocument.source,
                KBDocument.version,
            ).where(
                KBDocument.id.in_(doc_ids), KBDocument.is_delete == "0"
            )
        )
        meta: dict[str, dict] = {}
        for row in result.all():
            doc_id_str = str(row[0])
            source = row[5] if row[5] is not None else "v1_legacy"
            version = row[6] if row[6] is not None else 1
            meta[doc_id_str] = {
                "customer_id": row[1],
                "agent_id": row[2],
                "call_date": row[3],
                "risk_tag": row[4],
                "source": source,
                "version": version,
            }
        return meta

    async def get_summary(self, doc_id: uuid.UUID) -> str | None:
        """V4：查询单个文档的摘要（PG 权威源）；无摘要/文档不存在返回 None。"""
        result = await self.db.execute(
            select(KBDocument.summary).where(
                KBDocument.id == doc_id, KBDocument.is_delete == "0"
            )
        )
        return result.scalar_one_or_none()

    async def get_summaries_map(self, doc_ids: list[uuid.UUID]) -> dict[str, str | None]:
        """V4：批量查询文档摘要，返回 {doc_id_str: summary}（缺失文档不含键）。"""
        if not doc_ids:
            return {}
        result = await self.db.execute(
            select(KBDocument.id, KBDocument.summary).where(
                KBDocument.id.in_(doc_ids), KBDocument.is_delete == "0"
            )
        )
        return {str(row[0]): row[1] for row in result.all()}

    async def update_summary(self, doc_id: uuid.UUID, summary: str) -> None:
        """V4：更新文档摘要（PG 权威源收口，仅在 ES doc_summary 写成功后调用）。"""
        await self.db.execute(
            update(KBDocument)
            .where(KBDocument.id == doc_id, KBDocument.is_delete == "0")
            .values(summary=summary)
        )

    async def delete_assets_by_doc(self, doc_id: uuid.UUID) -> tuple[int, uuid.UUID]:
        """V5：级联清理 kb_doc_assets 资产记录（物理删除），返回 (删除行数, doc_id)。

        供删除文档 / reindex 路径调用；doc_id 用于调用方清理本地资产目录
        KB_ASSETS_DIR/{doc_id}/。删除失败由调用方按既有"删除失败重试与补偿"契约
        处理（重试 2 次 + 告警标记，同 _delete_with_retry 模式）。
        """
        result = await self.db.execute(
            delete(KbDocAsset).where(KbDocAsset.doc_id == doc_id)
        )
        return result.rowcount or 0, doc_id
