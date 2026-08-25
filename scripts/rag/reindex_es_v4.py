"""ES V4 索引重建脚本：kb_bm25 增补 doc_summary 字段（text, ik_max_word）并增量重建。

ES 不能直接为存量 index 追加 analyzer 字段，采用"临时索引 + _reindex 原地重建"：
    kb_bm25 → kb_bm25_v4_tmp（新 mapping，含 doc_summary）
    → 删除 kb_bm25 → 以新 mapping 重建 kb_bm25 → kb_bm25_v4_tmp → kb_bm25
    → 删除临时索引 → count 对比打印

使用方式：
    conda run -n ai_collection python scripts/rag/reindex_es_v4.py

前置条件：Elasticsearch 已启动，kb_bm25 索引存在。
注意事项：
  - 建议在维护窗口内执行（重建期间暂停写入，避免丢失增量）。
  - 重建完成前请勿开启摘要增强开关 KB_SUMMARY_ENABLED；
    重建完成后开启，正文 BM25 检索在重建前后不受影响。
  - 回滚：无需操作（kb_bm25 增补字段为纯增量，旧字段语义不变）。
"""
import asyncio
import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, ".")

import structlog
from elasticsearch import AsyncElasticsearch

from common.config.settings import settings

logger = structlog.get_logger()

SOURCE_INDEX = "kb_bm25"
TEMP_INDEX = "kb_bm25_v4_tmp"
MAPPING_FILE = Path(__file__).parent / "es_v4_mapping.json"


async def _create_index(client: AsyncElasticsearch, index: str) -> None:
    mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    await client.indices.create(index=index, body=mapping)
    logger.info("index_created", index=index)


async def reindex() -> None:
    client = AsyncElasticsearch(settings.es_url)

    exists = await client.indices.exists(index=SOURCE_INDEX)
    if not exists:
        logger.error("source_index_not_found", index=SOURCE_INDEX)
        await client.close()
        raise SystemExit(1)

    # 1. 清理可能残留的临时索引
    if await client.indices.exists(index=TEMP_INDEX):
        await client.indices.delete(index=TEMP_INDEX)
        logger.info("stale_temp_index_removed", index=TEMP_INDEX)

    # 2. 以新 mapping（含 doc_summary）创建临时索引并全量复制
    await _create_index(client, TEMP_INDEX)
    logger.info("reindex_started", source=SOURCE_INDEX, target=TEMP_INDEX)
    await client.reindex(
        body={"source": {"index": SOURCE_INDEX}, "dest": {"index": TEMP_INDEX}},
        wait_for_completion=True,
    )

    # 3. 删除旧索引，以新 mapping 原地重建 SOURCE_INDEX 并从临时索引回迁
    await client.indices.delete(index=SOURCE_INDEX)
    await _create_index(client, SOURCE_INDEX)
    logger.info("reindex_started", source=TEMP_INDEX, target=SOURCE_INDEX)
    await client.reindex(
        body={"source": {"index": TEMP_INDEX}, "dest": {"index": SOURCE_INDEX}},
        wait_for_completion=True,
    )
    await client.indices.delete(index=TEMP_INDEX)
    logger.info("temp_index_removed", index=TEMP_INDEX)

    # 4. count 对比（重建前后应一致）
    count = (await client.count(index=SOURCE_INDEX))["count"]
    logger.info("count_after_reindex", index=SOURCE_INDEX, count=count)

    await client.close()


if __name__ == "__main__":
    asyncio.run(reindex())
