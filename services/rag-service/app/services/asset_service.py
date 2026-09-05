"""V5 资产落库编排：OCR 文搜图 + 表格结构化入库 + kb_doc_assets 元数据（design D4/D5）。"""
import json
import shutil
import uuid
from pathlib import Path

import structlog

from app.models.orm import KbDocAsset
from app.parsers.asset_extractor import image_extract_enabled, table_extract_enabled
from common.config.settings import settings

logger = structlog.get_logger()

_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
}


def remove_assets_dir(doc_id: uuid.UUID) -> None:
    """删除本地资产目录 KB_ASSETS_DIR/{doc_id}/（幂等；失败忽略，交由调用方补偿契约）。"""
    path = Path(settings.KB_ASSETS_DIR) / str(doc_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


class AssetService:
    def __init__(
        self,
        doc_repo,
        chunk_repo,
        asset_repo,
        milvus_client,
        es_client,
        embed_client,
        ocr_client=None,
        redis=None,
    ):
        self.doc_repo = doc_repo
        self.chunk_repo = chunk_repo
        self.asset_repo = asset_repo
        self.milvus = milvus_client
        self.es = es_client
        self.embed = embed_client
        self._ocr_client = ocr_client
        self._redis = redis

    async def _get_ocr(self):
        """延迟初始化 OCR 客户端（生产 DI 注入，测试可传 mock）。"""
        if self._ocr_client is None:
            from app.api.deps import get_ocr_client
            self._ocr_client = await get_ocr_client()
        return self._ocr_client

    @staticmethod
    def _filter_assets_by_switch(assets: list[dict]) -> list[dict]:
        """落库层再过滤：只保留与当前开关匹配的类型。"""
        filtered: list[dict] = []
        for asset in assets:
            asset_type = asset.get("type")
            if asset_type == "image" and not image_extract_enabled():
                continue
            if asset_type == "table" and not table_extract_enabled():
                continue
            filtered.append(asset)
        return filtered

    async def process_assets(self, doc_id: uuid.UUID, assets: list[dict], category: str) -> None:
        """按资产类型落库：图片走 OCR 文搜图，表格走结构化入库。

        单张 OCR/落盘失败仅 warning 并跳过，不阻断正文；批量 embedding/双写失败向上抛出。
        """
        assets = self._filter_assets_by_switch(assets)
        if not assets:
            return

        self._next_chunk_index = await self.chunk_repo.next_chunk_index(doc_id)
        pending_chunks: list[str] = []
        pending_meta: list[KbDocAsset] = []

        for asset in assets:
            try:
                if asset["type"] == "image":
                    meta, chunk_text = await self._prepare_image(doc_id, asset)
                else:
                    meta, chunk_text = await self._prepare_table(doc_id, asset)
                if meta is not None:
                    pending_meta.append(meta)
                if chunk_text:
                    if isinstance(chunk_text, list):
                        pending_chunks.extend(chunk_text)
                    else:
                        pending_chunks.append(chunk_text)
            except Exception as e:
                # 单资产失败仅 skip：禁止对共享 Session 全量 rollback（会滚掉同事务内
                # 已 flush 的 kb_documents，导致后续批量写 kb_chunks 触发 FK 失败）。
                logger.warning(
                    "asset_process_skipped",
                    doc_id=str(doc_id),
                    asset_type=asset.get("type"),
                    error=str(e),
                )

        if pending_chunks:
            await self._batch_index_chunks(doc_id, category, pending_chunks)

        if pending_meta:
            await self.asset_repo.batch_create(pending_meta)

    async def _prepare_image(
        self, doc_id: uuid.UUID, asset: dict
    ) -> tuple[KbDocAsset | None, str | None]:
        binary = asset["binary"]
        mime = asset.get("mime") or "image/png"
        page = asset.get("page") or 0

        ocr = await self._get_ocr()
        ocr_text = await ocr.recognize(binary, f"asset_{doc_id}_p{page}")

        ext = _EXT_BY_MIME.get(mime, "png")
        rel = f"{doc_id}/{page}_a_{uuid.uuid4().hex[:8]}.{ext}"
        abs_path = Path(settings.KB_ASSETS_DIR) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(binary)

        chunk_text = (
            f"[图片] 页码P{page}: {ocr_text.strip()}" if ocr_text.strip() else None
        )
        meta = KbDocAsset(
            doc_id=doc_id,
            asset_type="image",
            page=page,
            storage_path=rel,
            ocr_text=ocr_text,
            mime_type=mime,
        )
        return meta, chunk_text

    async def _prepare_table(
        self, doc_id: uuid.UUID, asset: dict
    ) -> tuple[KbDocAsset | None, list[str] | None]:
        headers = asset.get("headers") or []
        rows = asset.get("rows") or []
        serialized_lines = asset.get("serialized_lines") or []
        page = asset.get("page") or 0
        if not serialized_lines:
            return None, None

        chunk_lines = list(serialized_lines)
        summary_line = asset.get("summary_line")
        if summary_line:
            chunk_lines.append(summary_line)

        content_json = json.dumps({"headers": headers, "rows": rows}, ensure_ascii=False)
        rel = f"{doc_id}/{page}_t_{uuid.uuid4().hex[:8]}.json"
        abs_path = Path(settings.KB_ASSETS_DIR) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content_json, encoding="utf-8")

        meta = KbDocAsset(
            doc_id=doc_id,
            asset_type="table",
            page=page,
            storage_path=rel,
            ocr_text="\n".join(serialized_lines),
            mime_type="application/json",
            content_json=content_json,
        )
        return meta, chunk_lines

    async def _batch_index_chunks(
        self, doc_id: uuid.UUID, category: str, contents: list[str]
    ) -> None:
        """同一文档资产阶段批量 embedding + 双写（仍保持一行/一图一 chunk）。"""
        if not contents:
            return

        chunk_ids = [uuid.uuid4() for _ in contents]
        start_index = getattr(self, "_next_chunk_index", None)
        if start_index is None:
            start_index = await self.chunk_repo.next_chunk_index(doc_id)

        vectors = await self.embed.batch_embed(contents)
        metadata = [
            {"chunk_id": str(cid), "doc_id": str(doc_id), "category": category}
            for cid in chunk_ids
        ]
        vector_ids = await self.milvus.insert(vectors, metadata)
        await self.es.index_docs([
            {
                "chunk_id": str(chunk_ids[i]),
                "content": contents[i],
                "doc_id": str(doc_id),
                "category": category,
                "doc_summary": "",
            }
            for i in range(len(contents))
        ])
        await self.chunk_repo.batch_create([
            {
                "id": chunk_ids[i],
                "doc_id": doc_id,
                "chunk_index": start_index + i,
                "content": contents[i],
                "vector_id": vector_ids[i],
                "category": category,
            }
            for i in range(len(contents))
        ])
        self._next_chunk_index = start_index + len(contents)
