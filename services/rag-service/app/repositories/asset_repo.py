"""kb_doc_assets 数据访问层（V5 内嵌资产元数据，纯 CRUD 无业务逻辑）。"""
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import KbDocAsset


class AssetRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def batch_create(self, assets: list[KbDocAsset]) -> int:
        """批量写入资产元数据，返回写入行数（空列表短路，返回 0）。"""
        if not assets:
            return 0
        self.db.add_all(assets)
        await self.db.flush()
        return len(assets)

    async def delete_by_doc(self, doc_id: uuid.UUID) -> int:
        """删除某文档全部资产记录（物理删除），返回删除行数。"""
        result = await self.db.execute(
            delete(KbDocAsset).where(KbDocAsset.doc_id == doc_id)
        )
        return result.rowcount or 0

    async def get_by_doc(self, doc_id: uuid.UUID) -> list[KbDocAsset]:
        """按文档查询全部资产（未删除），按页号/ID 升序。"""
        result = await self.db.execute(
            select(KbDocAsset)
            .where(KbDocAsset.doc_id == doc_id, KbDocAsset.is_delete == "0")
            .order_by(KbDocAsset.page, KbDocAsset.id)
        )
        return list(result.scalars().all())

    async def get_by_doc_page(self, doc_id: uuid.UUID, page: int) -> list[KbDocAsset]:
        """按文档+页码查询资产（未删除），按 ID 升序。"""
        result = await self.db.execute(
            select(KbDocAsset)
            .where(
                KbDocAsset.doc_id == doc_id,
                KbDocAsset.page == page,
                KbDocAsset.is_delete == "0",
            )
            .order_by(KbDocAsset.id)
        )
        return list(result.scalars().all())
