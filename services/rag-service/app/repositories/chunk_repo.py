"""kb_chunks 数据访问层。"""
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import KBChunk


class ChunkRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def next_chunk_index(self, doc_id: uuid.UUID) -> int:
        """返回该文档下一个可用 chunk_index（max+1；无记录时为 0）。

        资产 chunk 追加入库时使用，避免与正文 chunk_index=0 唯一约束冲突。
        """
        result = await self.db.execute(
            select(func.max(KBChunk.chunk_index)).where(
                KBChunk.doc_id == doc_id,
                KBChunk.is_delete == "0",
            )
        )
        current = result.scalar_one_or_none()
        return 0 if current is None else int(current) + 1

    async def batch_create(self, chunks: list[dict]) -> int:
        if not chunks:
            return 0
        entities = [
            KBChunk(
                id=chunk["id"],
                doc_id=chunk["doc_id"],
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                vector_id=chunk["vector_id"],
                category=chunk["category"],
            )
            for chunk in chunks
        ]
        self.db.add_all(entities)
        await self.db.flush()
        return len(entities)

    async def delete_by_doc(self, doc_id: uuid.UUID) -> int:
        result = await self.db.execute(
            delete(KBChunk).where(KBChunk.doc_id == doc_id)
        )
        return result.rowcount

    async def get_by_ids(self, chunk_ids: list[uuid.UUID]) -> list[KBChunk]:
        if not chunk_ids:
            return []
        result = await self.db.execute(
            select(KBChunk).where(
                KBChunk.id.in_(chunk_ids), KBChunk.is_delete == "0"
            )
        )
        return list(result.scalars().all())

    async def get_by_vector_ids(self, vector_ids: list[str]) -> list[KBChunk]:
        if not vector_ids:
            return []
        result = await self.db.execute(
            select(KBChunk).where(KBChunk.vector_id.in_(vector_ids))
        )
        return list(result.scalars().all())
