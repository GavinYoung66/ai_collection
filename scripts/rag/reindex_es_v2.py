"""ES V2 索引 reindex 脚本：从 kb_bm25 → kb_chunks_bm25。

使用 ES _reindex API 全量复制，完成后 count 对比打印。

使用方式：
    conda run -n ai_collection python scripts/rag/reindex_es_v2.py

前置条件：Elasticsearch 已启动，kb_bm25 索引存在。
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
TARGET_INDEX = "kb_chunks_bm25"
MAPPING_FILE = Path(__file__).parent / "es_v2_mapping.json"


async def reindex() -> None:
    client = AsyncElasticsearch(settings.es_url)

    # 1. 创建目标索引（若不存在）
    exists = await client.indices.exists(index=TARGET_INDEX)
    if not exists:
        mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
        await client.indices.create(index=TARGET_INDEX, body=mapping)
        logger.info("target_index_created", index=TARGET_INDEX)
    else:
        logger.info("target_index_already_exists", index=TARGET_INDEX)

    # 2. _reindex API 全量复制
    logger.info("reindex_started", source=SOURCE_INDEX, target=TARGET_INDEX)
    result = await client.reindex(
        body={
            "source": {"index": SOURCE_INDEX},
            "dest": {"index": TARGET_INDEX},
        },
        wait_for_completion=True,
    )
    logger.info("reindex_completed", result=result)

    # 3. count 对比
    source_count = (await client.count(index=SOURCE_INDEX))["count"]
    target_count = (await client.count(index=TARGET_INDEX))["count"]
    logger.info(
        "count_comparison",
        source=SOURCE_INDEX,
        source_count=source_count,
        target=TARGET_INDEX,
        target_count=target_count,
        diff=abs(source_count - target_count),
    )

    await client.close()


if __name__ == "__main__":
    asyncio.run(reindex())
