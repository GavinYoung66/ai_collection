"""种子数据：敏感词词库（threat/abuse/induce 三分类）。"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.base import Base
from common.db.session import async_session_factory, engine

SENSITIVE_WORDS = [
    # threat（high）
    {"word": "上门催收", "category": "threat", "severity": "high", "description": "威胁上门"},
    {"word": "影响征信", "category": "threat", "severity": "high", "description": "以征信恐吓"},
    {"word": "通知通讯录", "category": "threat", "severity": "high", "description": "威胁爆通讯录"},
    {"word": "起诉坐牢", "category": "threat", "severity": "high", "description": "威胁起诉"},
    {"word": "冻结账户", "category": "threat", "severity": "high", "description": "威胁冻结"},
    {"word": "抓人", "category": "threat", "severity": "high", "description": "威胁抓捕"},
    {"word": "找你家人", "category": "threat", "severity": "high", "description": "威胁联系家人"},
    {"word": "走法律程序", "category": "threat", "severity": "high", "description": "威胁法律"},
    {"word": "强制执行", "category": "threat", "severity": "high", "description": "威胁强制执行"},
    {"word": "拍卖房产", "category": "threat", "severity": "high", "description": "威胁拍卖"},
    {"word": "限制高消费", "category": "threat", "severity": "high", "description": "威胁限高"},
    {"word": "拉黑名单", "category": "threat", "severity": "high", "description": "威胁黑名单"},
    {"word": "告诉你单位", "category": "threat", "severity": "high", "description": "威胁通知单位"},
    {"word": "拘留", "category": "threat", "severity": "high", "description": "威胁拘留"},
    {"word": "判刑", "category": "threat", "severity": "high", "description": "威胁判刑"},
    {"word": "坐牢", "category": "threat", "severity": "high", "description": "威胁坐牢"},
    {"word": "报警抓你", "category": "threat", "severity": "high", "description": "威胁报警"},
    {"word": "上门找你", "category": "threat", "severity": "high", "description": "威胁上门"},
    {"word": "失信被执行人", "category": "threat", "severity": "high", "description": "威胁失信"},
    {"word": "限制出行", "category": "threat", "severity": "high", "description": "威胁限行"},

    # abuse（medium）
    {"word": "蠢货", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "废物", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "无赖", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "赖皮", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "不要脸", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "厚颜无耻", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "滚", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "烦人", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "脑子有病", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "神经病", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "白痴", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "混蛋", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "猪脑子", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "没素质", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "骗子", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "老赖", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "死皮赖脸", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "无耻", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "欠揍", "category": "abuse", "severity": "medium", "description": "辱骂"},
    {"word": "该死", "category": "abuse", "severity": "medium", "description": "辱骂"},

    # induce（low）
    {"word": "可以减免", "category": "induce", "severity": "low", "description": "诱导减免"},
    {"word": "不用还了", "category": "induce", "severity": "low", "description": "诱导不还"},
    {"word": "帮你消账", "category": "induce", "severity": "low", "description": "诱导消账"},
    {"word": "帮你销账", "category": "induce", "severity": "low", "description": "诱导销账"},
    {"word": "不用利息", "category": "induce", "severity": "low", "description": "诱导免息"},
    {"word": "免息分期", "category": "induce", "severity": "low", "description": "诱导免息"},
    {"word": "少还点", "category": "induce", "severity": "low", "description": "诱导少还"},
    {"word": "帮你处理", "category": "induce", "severity": "low", "description": "诱导处理"},
    {"word": "内部渠道", "category": "induce", "severity": "low", "description": "诱导内部"},
    {"word": "特殊渠道", "category": "induce", "severity": "low", "description": "诱导特殊"},
]


async def seed():
    from sqlalchemy import text

    async with async_session_factory() as session:
        for w in SENSITIVE_WORDS:
            existing = await session.execute(
                text("SELECT id FROM sensitive_words WHERE word = :word"),
                {"word": w["word"]},
            )
            if existing.scalar_one_or_none() is not None:
                print(f"  skip: {w['word']}")
                continue

            await session.execute(
                text(
                    "INSERT INTO sensitive_words (word, category, severity, status, description) "
                    "VALUES (:word, :category, :severity, 'active', :description)"
                ),
                w,
            )
            print(f"  created: {w['word']} ({w['category']}/{w['severity']})")

        await session.commit()
        print(f"Done: {len(SENSITIVE_WORDS)} sensitive words processed.")


if __name__ == "__main__":
    asyncio.run(seed())
