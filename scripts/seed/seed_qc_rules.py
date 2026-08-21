"""种子数据：质检规则。"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.session import async_session_factory

QC_RULES = [
    {"rule_type": "keyword", "pattern": "上门催收|影响征信|通知通讯录|起诉坐牢", "severity": "high", "score_deduct": 20, "description": "威胁类违规扣20分"},
    {"rule_type": "keyword", "pattern": "蠢货|废物|无赖|不要脸|滚", "severity": "medium", "score_deduct": 15, "description": "辱骂类违规扣15分"},
    {"rule_type": "keyword", "pattern": "可以减免|不用还了|帮你消账|免息", "severity": "low", "score_deduct": 10, "description": "诱导类违规扣10分"},
    {"rule_type": "semantic", "pattern": "未在开场报明身份", "severity": "low", "score_deduct": 5, "description": "未报身份扣5分"},
    {"rule_type": "semantic", "pattern": "编造或虚报欠款金额", "severity": "high", "score_deduct": 25, "description": "编造金额扣25分"},
    {"rule_type": "duration", "pattern": "通话时长<30秒", "severity": "medium", "score_deduct": 10, "description": "通话过短扣10分"},
    {"rule_type": "silence", "pattern": "单轮静默>10秒", "severity": "low", "score_deduct": 5, "description": "静默过长扣5分"},
    {"rule_type": "regex", "pattern": "^(?!.*您好).*$", "severity": "low", "score_deduct": 5, "description": "开场无问候扣5分"},
]


async def seed():
    async with async_session_factory() as session:
        for rule in QC_RULES:
            existing = await session.execute(
                text("SELECT id FROM qc_rules WHERE description = :description"),
                {"description": rule["description"]},
            )
            if existing.scalar_one_or_none() is not None:
                print(f"  skip: {rule['description']}")
                continue

            await session.execute(
                text(
                    "INSERT INTO qc_rules (rule_type, pattern, severity, score_deduct, status, description) "
                    "VALUES (:rule_type, :pattern, :severity, :score_deduct, 'active', :description)"
                ),
                rule,
            )
            print(f"  created: {rule['description']} (-{rule['score_deduct']})")

        await session.commit()
        print(f"Done: {len(QC_RULES)} QC rules processed. Pass line: 80 points.")


if __name__ == "__main__":
    asyncio.run(seed())
