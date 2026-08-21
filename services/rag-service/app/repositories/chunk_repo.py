"""kb_chunks 数据访问层。"""
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import KBChunk


class ChunkRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

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
