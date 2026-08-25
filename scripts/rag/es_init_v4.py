"""ES V4 索引幂等初始化（冒烟/开发环境 one-shot 用，生产可选）。

目标：确保 kb_bm25 索引存在且 mapping 含 doc_summary（text, ik_max_word），
使 KB_SUMMARY_ENABLED=true 时 ES doc_summary 加权查询不会因字段缺失报 400。

幂等策略（可重复执行）：
- kb_bm25 不存在                → 按 es_v4_mapping.json 创建（含 doc_summary）
- 存在且 mapping 含 doc_summary → 跳过（已就绪）
- 存在但缺 doc_summary          → 临时索引重建（对齐 reindex_es_v4.py 的
  "tmp + 原地重建 + 回迁" 逻辑，保留既有文档）

使用方式：
    PYTHONPATH=. python scripts/rag/es_init_v4.py
退出码：0=就绪/已就绪，非 0=失败（供 compose depends_on 门控）。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from elasticsearch import AsyncElasticsearch  # noqa: E402

from common.config.settings import settings  # noqa: E402

SOURCE_INDEX = "kb_bm25"
TEMP_INDEX = "kb_bm25_v4_tmp"
MAPPING_FILE = Path(__file__).parent / "es_v4_mapping.json"


async def _create_index(client: AsyncElasticsearch, index: str) -> None:
    mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    await client.indices.create(index=index, body=mapping)
    print(f"[es-init] index created: {index}")


async def _has_doc_summary(client: AsyncElasticsearch, index: str) -> bool:
    mapping = await client.indices.get_mapping(index=index)
    props = mapping.get(index, {}).get("mappings", {}).get("properties", {})
    return "doc_summary" in props


async def ensure() -> None:
    client = AsyncElasticsearch(settings.es_url)

    exists = await client.indices.exists(index=SOURCE_INDEX)
    if not exists:
        await _create_index(client, SOURCE_INDEX)
    elif await _has_doc_summary(client, SOURCE_INDEX):
        print(f"[es-init] {SOURCE_INDEX} already has doc_summary, skip")
    else:
        print(f"[es-init] {SOURCE_INDEX} missing doc_summary, rebuilding...")
        if await client.indices.exists(index=TEMP_INDEX):
            await client.indices.delete(index=TEMP_INDEX)
        await _create_index(client, TEMP_INDEX)
        await client.reindex(
            body={"source": {"index": SOURCE_INDEX}, "dest": {"index": TEMP_INDEX}},
            wait_for_completion=True,
        )
        await client.indices.delete(index=SOURCE_INDEX)
        await _create_index(client, SOURCE_INDEX)
        await client.reindex(
            body={"source": {"index": TEMP_INDEX}, "dest": {"index": SOURCE_INDEX}},
            wait_for_completion=True,
        )
        await client.indices.delete(index=TEMP_INDEX)

    count = (await client.count(index=SOURCE_INDEX))["count"]
    print(f"[es-init] done, {SOURCE_INDEX} count={count}")
    await client.close()


if __name__ == "__main__":
    asyncio.run(ensure())
