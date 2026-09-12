"""qc_violations 增加可空 source / word / kb_snippet（演示 hits 字段）。

MUST NOT 改 0011；MUST NOT 把这些字段塞回 qc_records.violations JSONB。

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qc_violations
            ADD COLUMN IF NOT EXISTS source VARCHAR(10),
            ADD COLUMN IF NOT EXISTS word VARCHAR(255),
            ADD COLUMN IF NOT EXISTS kb_snippet TEXT;
        """
    )
    op.execute(
        """
        ALTER TABLE qc_violations
            ADD CONSTRAINT qc_violations_source_check
            CHECK (source IS NULL OR source IN ('ac','rule','llm'));
        """
    )
    # asyncpg 不能在一条 prepared statement 里跑多条 SQL
    op.execute("COMMENT ON COLUMN qc_violations.source IS '命中来源 ac|rule|llm';")
    op.execute("COMMENT ON COLUMN qc_violations.word IS 'AC 命中词';")
    op.execute("COMMENT ON COLUMN qc_violations.kb_snippet IS '检索条款原文；无检索可空';")


def downgrade() -> None:
    op.execute("ALTER TABLE qc_violations DROP CONSTRAINT IF EXISTS qc_violations_source_check;")
    op.execute("ALTER TABLE qc_violations DROP COLUMN IF EXISTS kb_snippet;")
    op.execute("ALTER TABLE qc_violations DROP COLUMN IF EXISTS word;")
    op.execute("ALTER TABLE qc_violations DROP COLUMN IF EXISTS source;")
