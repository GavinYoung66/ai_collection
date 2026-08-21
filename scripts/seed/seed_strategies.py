"""种子数据：策略版本 + 技能标签 + Redis 预热。"""
import asyncio
import json

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.config.settings import settings
from common.db.session import async_session_factory

STRATEGY_VERSIONS = [
    {
        "strategy_type": "call_flow",
        "params": {
            "max_dialog_turns": 15,
            "confidence_threshold": 0.7,
            "transfer_on_low_confidence": True,
            "fallback_script": "system_error",
        },
        "version": 1,
        "status": "active",
    },
    {
        "strategy_type": "assignment",
        "params": {
            "weights": {"willingness": 0.4, "ability": 0.3, "complaint": 0.3},
            "risk_levels": ["low", "medium", "high"],
            "auto_assign": True,
        },
        "version": 1,
        "status": "active",
    },
]

SKILL_TAGS = [
    {"name": "催收话术", "description": "基础催收话术技能"},
    {"name": "谈判", "description": "高级谈判技巧"},
    {"name": "安抚", "description": "客户情绪安抚"},
    {"name": "法律", "description": "法律知识应用"},
    {"name": "投诉处理", "description": "投诉应对与处理"},
    {"name": "培训", "description": "新坐席培训辅导"},
]


async def seed():
    async with async_session_factory() as session:
        for sv in STRATEGY_VERSIONS:
            existing = await session.execute(
                text(
                    "SELECT id FROM strategy_versions "
                    "WHERE strategy_type = :st AND version = :v"
                ),
                {"st": sv["strategy_type"], "v": sv["version"]},
            )
            if existing.scalar_one_or_none() is not None:
                print(f"  skip: {sv['strategy_type']} v{sv['version']}")
                continue

            await session.execute(
                text(
                    "INSERT INTO strategy_versions (strategy_type, params, version, status) "
                    "VALUES (:st, :params, :v, :status)"
                ),
                {
                    "st": sv["strategy_type"],
                    "params": json.dumps(sv["params"]),
                    "v": sv["version"],
                    "status": sv["status"],
                },
            )
            print(f"  created: {sv['strategy_type']} v{sv['version']} ({sv['status']})")

        await session.commit()

    redis_client = aioredis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB
    )
    await redis_client.set("dp:strategy:active", "1")
    await redis_client.set("cs:skill_tags:active", json.dumps([s["name"] for s in SKILL_TAGS]))
    await redis_client.close()
    print(f"Redis warmed: dp:strategy:active=1, cs:skill_tags:active={len(SKILL_TAGS)} tags")
    print(f"Done: {len(STRATEGY_VERSIONS)} strategies + {len(SKILL_TAGS)} skill tags.")


if __name__ == "__main__":
    asyncio.run(seed())
