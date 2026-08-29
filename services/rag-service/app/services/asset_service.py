"""V5 资产落库编排：OCR 文搜图 + 表格结构化入库 + kb_doc_assets 元数据（design D4/D5）。"""
import json
import shutil
import uuid
from pathlib import Path

import structlog

from app.models.orm import KbDocAsset
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

    async def process_assets(self, doc_id: uuid.UUID, assets: list[dict], category: str) -> None:
        """按资产类型落库：图片走 OCR 文搜图，表格走结构化入库。

        单张失败仅 warning（含 trace_id）并跳过，不阻断正文（正文已入库不回滚）。
        """
        # 从正文已占用的 chunk_index 之后递增，避免 (doc_id, chunk_index) 唯一约束冲突
        self._next_chunk_index = await self.chunk_repo.next_chunk_index(doc_id)
        for asset in assets:
            try:
                if asset["type"] == "image":
                    await self._process_image(doc_id, asset, category)
                else:
                    await self._process_table(doc_id, asset, category)
            except Exception as e:
                # flush 失败会污染 AsyncSession；回滚后才能继续处理其余资产与正文 active
                try:
                    await self.chunk_repo.db.rollback()
                except Exception:
                    pass
                logger.warning(
                    "asset_process_skipped",
                    doc_id=str(doc_id),
                    asset_type=asset.get("type"),
                    error=str(e),
                )

    async def _process_image(self, doc_id: uuid.UUID, asset: dict, category: str) -> None:
        binary = asset["binary"]
        mime = asset.get("mime") or "image/png"
        page = asset.get("page") or 0

        # 1) OCR 识别（ocr-service:8200，30s 超时/3 次退避/熔断 rag:ocr:degraded，复用既有 OCRClient）
        ocr = await self._get_ocr()
        ocr_text = await ocr.recognize(binary, f"asset_{doc_id}_p{page}")

        # 2) 本地文件落 KB_ASSETS_DIR/{doc_id}/（相对路径入库，Gradio 经共享 volume 读取）
        ext = _EXT_BY_MIME.get(mime, "png")
        rel = f"{doc_id}/{page}_a_{uuid.uuid4().hex[:8]}.{ext}"
        abs_path = Path(settings.KB_ASSETS_DIR) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(binary)

        # 3) OCR 文本以普通 chunk 走既有双索引入库（文搜图，source 语义经前缀标记）
        if ocr_text.strip():
            await self._index_chunk(doc_id, category, f"[图片] 页码P{page}: {ocr_text.strip()}")

        # 4) kb_doc_assets 元数据
        await self.asset_repo.batch_create([
            KbDocAsset(
                doc_id=doc_id,
                asset_type="image",
                page=page,
                storage_path=rel,
                ocr_text=ocr_text,
                mime_type=mime,
            )
        ])

    async def _process_table(self, doc_id: uuid.UUID, asset: dict, category: str) -> None:
        headers = asset.get("headers") or []
        rows = asset.get("rows") or []
        serialized_lines = asset.get("serialized_lines") or []
        page = asset.get("page") or 0
        if not serialized_lines:
            return  # 空表/无数据行：不落库

        # 1) 每行序列化文本以 chunk 双索引入库（保留行列结构不打散）
        for line in serialized_lines:
            await self._index_chunk(doc_id, category, line)

        # 2) 结构化预览源（content_json 供 gr.Dataframe 渲染，单一数据源）+ 元数据
        content_json = json.dumps({"headers": headers, "rows": rows}, ensure_ascii=False)
        rel = f"{doc_id}/{page}_t_{uuid.uuid4().hex[:8]}.json"
        abs_path = Path(settings.KB_ASSETS_DIR) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content_json, encoding="utf-8")

        await self.asset_repo.batch_create([
            KbDocAsset(
                doc_id=doc_id,
                asset_type="table",
                page=page,
                storage_path=rel,
                ocr_text="\n".join(serialized_lines),
                mime_type="application/json",
                content_json=content_json,
            )
        ])

    async def _index_chunk(self, doc_id: uuid.UUID, category: str, content: str) -> None:
        """单条资产 chunk 走既有双索引入库：embed → Milvus → ES → kb_chunks。"""
        chunk_id = uuid.uuid4()
        chunk_index = getattr(self, "_next_chunk_index", None)
        if chunk_index is None:
            chunk_index = await self.chunk_repo.next_chunk_index(doc_id)
        vectors = await self.embed.batch_embed([content])
        vector_ids = await self.milvus.insert(
            vectors, [{"chunk_id": str(chunk_id), "doc_id": str(doc_id), "category": category}]
        )
        await self.es.index_docs([
            {
                "chunk_id": str(chunk_id),
                "content": content,
                "doc_id": str(doc_id),
                "category": category,
                "doc_summary": "",
            }
        ])
        await self.chunk_repo.batch_create([
            {
                "id": chunk_id,
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "vector_id": vector_ids[0],
                "category": category,
            }
        ])
        self._next_chunk_index = chunk_index + 1
