"""kb_documents 数据访问层。"""
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import KBChunk, KBDocument


class DocRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, title: str, category: str, content: str, created_by: uuid.UUID | None = None
    ) -> uuid.UUID:
        doc = KBDocument(
            title=title,
            category=category,
            content=content,
            status="processing",
            created_by=created_by,
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

        items = []
        for doc in docs:
            chunks_count = await self.get_chunks_count(doc.id)
            items.append({
                "id": str(doc.id),
                "title": doc.title,
                "category": doc.category,
                "status": doc.status,
                "chunks_count": chunks_count,
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
