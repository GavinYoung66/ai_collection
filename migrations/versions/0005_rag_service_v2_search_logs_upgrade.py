"""rag-service v2: kb_search_logs BOOLEAN→VARCHAR 双写过渡 + 5 新字段

发布窗口内（Decision 10）仅新增 tmp 列并回填，**不 DROP 旧 BOOLEAN 列、不 RENAME tmp 列**，
旧 BOOLEAN 列保留作为 V1 rollback 写入目标。DROP+RENAME 由 0006_post_cleanup 在
发布 30 天观察期通过后执行。

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 1. 新增 5 个字段 ===
    op.execute("""
        ALTER TABLE kb_search_logs
        ADD COLUMN IF NOT EXISTS query_original VARCHAR(1000) NULL,
        ADD COLUMN IF NOT EXISTS query_rewritten VARCHAR(1000) NULL,
        ADD COLUMN IF NOT EXISTS intent_tag VARCHAR(100) NULL,
        ADD COLUMN IF NOT EXISTS session_id VARCHAR(64) NULL;
    """)

    # === 2. BOOLEAN → VARCHAR 双写过渡：仅新增 tmp 列 + 回填 ===
    # 发布窗口内保留旧 BOOLEAN 列（degraded/is_fallback）作为 V1 rollback 写入目标；
    # DROP 旧列 + RENAME tmp→final 延后至 0006_post_cleanup（README Runbook Step 6）。
    # degraded: BOOLEAN → VARCHAR(1) '0'/'1'
    op.execute("""
        ALTER TABLE kb_search_logs
        ADD COLUMN IF NOT EXISTS degraded_tmp VARCHAR(1) NOT NULL DEFAULT '0'
        CHECK (degraded_tmp IN ('0','1'));
    """)
    op.execute("""
        UPDATE kb_search_logs SET degraded_tmp = CASE WHEN degraded IS TRUE THEN '1' ELSE '0' END;
    """)

    # is_fallback: BOOLEAN → VARCHAR(1) '0'/'1'
    op.execute("""
        ALTER TABLE kb_search_logs
        ADD COLUMN IF NOT EXISTS is_fallback_tmp VARCHAR(1) NOT NULL DEFAULT '0'
        CHECK (is_fallback_tmp IN ('0','1'));
    """)
    op.execute("""
        UPDATE kb_search_logs SET is_fallback_tmp = CASE WHEN is_fallback IS TRUE THEN '1' ELSE '0' END;
    """)

    # === 3. 回填 query_original（从旧 query 列复制） ===
    op.execute("""
        UPDATE kb_search_logs SET query_original = query WHERE query_original IS NULL;
    """)

    # === 4. 新增索引 ===
    op.execute("CREATE INDEX IF NOT EXISTS idx_kb_search_logs_session ON kb_search_logs(session_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_kb_search_logs_intent ON kb_search_logs(intent_tag);")


def downgrade() -> None:
    # 反向回滚至 0004 状态：BOOLEAN 旧列全程未动，仅需删除 tmp 列 + 5 新列 + 索引
    op.execute("DROP INDEX IF EXISTS idx_kb_search_logs_intent;")
    op.execute("DROP INDEX IF EXISTS idx_kb_search_logs_session;")

    # 删除 tmp 过渡列（旧 BOOLEAN degraded/is_fallback 保留不动）
    op.execute("ALTER TABLE kb_search_logs DROP COLUMN IF EXISTS is_fallback_tmp;")
    op.execute("ALTER TABLE kb_search_logs DROP COLUMN IF EXISTS degraded_tmp;")

    # 删除 5 新列
    op.execute("""
        ALTER TABLE kb_search_logs
        DROP COLUMN IF EXISTS session_id,
        DROP COLUMN IF EXISTS intent_tag,
        DROP COLUMN IF EXISTS query_rewritten,
        DROP COLUMN IF EXISTS query_original;
    """)
