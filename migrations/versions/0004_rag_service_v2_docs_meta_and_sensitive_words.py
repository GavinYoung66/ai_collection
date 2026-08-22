"""rag-service v2: kb_documents 元数据列 + sensitive_words seed

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 50 词基线（与 data/sensitive_words.dict 完全一致，逐行对应）
_SEED_WORDS = [
    "上门催收", "影响征信", "通知通讯录", "起诉坐牢", "冻结账户",
    "抓人", "找你家人", "走法律程序", "强制执行", "拍卖房产",
    "限制高消费", "拉黑名单", "告诉你单位", "拘留", "判刑",
    "坐牢", "报警抓你", "上门找你", "失信被执行人", "限制出行",
    "蠢货", "废物", "无赖", "赖皮", "不要脸",
    "厚颜无耻", "滚", "烦人", "脑子有病", "神经病",
    "白痴", "混蛋", "猪脑子", "没素质", "骗子",
    "老赖", "死皮赖脸", "无耻", "欠揍", "该死",
    "可以减免", "不用还了", "帮你消账", "帮你销账", "不用利息",
    "免息分期", "少还点", "帮你处理", "内部渠道", "特殊渠道",
]


def upgrade() -> None:
    # === 1. kb_documents 增元数据列（V2 多模态 + 业务加权所需） ===
    op.execute("""
        ALTER TABLE kb_documents
        ADD COLUMN IF NOT EXISTS customer_id UUID NULL,
        ADD COLUMN IF NOT EXISTS agent_id UUID NULL,
        ADD COLUMN IF NOT EXISTS call_date TIMESTAMP WITH TIME ZONE NULL,
        ADD COLUMN IF NOT EXISTS risk_tag VARCHAR(32) NULL
            CHECK (risk_tag IN ('high','medium','low')),
        ADD COLUMN IF NOT EXISTS source VARCHAR(32) NULL;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_kb_documents_customer_id ON kb_documents(customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_kb_documents_agent_id ON kb_documents(agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_kb_documents_call_date ON kb_documents(call_date DESC);")

    # === 2. sensitive_words seed 50 词基线 ===
    # 表已在 0001 创建（DDL 严格对齐 step4/01 §三.9），此处仅 seed
    for word in _SEED_WORDS:
        op.execute(
            f"INSERT INTO sensitive_words (id, word, category, severity, status, description, "
            f"create_time, update_time, is_delete) "
            f"VALUES (uuid_generate_v4(), '{word}', 'threat', 'medium', 'active', "
            f"'seed_migrated_from_v1_dict', NOW(), NOW(), '0') "
            f"ON CONFLICT (word) DO NOTHING;"
        )

    # 回填存量 V1 文档 source 字段
    op.execute(
        "UPDATE kb_documents SET source = 'v1_legacy' WHERE source IS NULL;"
    )


def downgrade() -> None:
    # 删除 seed 数据（只删 seed 标记的行，保留后续手动添加的）
    op.execute(
        "DELETE FROM sensitive_words WHERE description = 'seed_migrated_from_v1_dict';"
    )
    op.execute("DROP INDEX IF EXISTS idx_kb_documents_call_date;")
    op.execute("DROP INDEX IF EXISTS idx_kb_documents_agent_id;")
    op.execute("DROP INDEX IF EXISTS idx_kb_documents_customer_id;")
    op.execute("""
        ALTER TABLE kb_documents
        DROP COLUMN IF EXISTS source,
        DROP COLUMN IF EXISTS risk_tag,
        DROP COLUMN IF EXISTS call_date,
        DROP COLUMN IF EXISTS agent_id,
        DROP COLUMN IF EXISTS customer_id;
    """)
