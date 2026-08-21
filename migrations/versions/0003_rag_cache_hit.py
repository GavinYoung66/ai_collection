"""rag-service v1 整改: kb_search_logs add cache_hit (V1-T20)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 缓存命中标记：对齐 step4/01 类型规范，用 CHAR(1) '0'/'1' + CHECK，禁止 BOOLEAN
    op.execute("""
        ALTER TABLE kb_search_logs ADD COLUMN cache_hit CHAR(1) NOT NULL DEFAULT '0'
        CHECK (cache_hit IN ('0','1'));
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE kb_search_logs DROP COLUMN IF EXISTS cache_hit;")
