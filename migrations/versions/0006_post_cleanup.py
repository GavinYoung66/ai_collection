"""post-cleanup: kb_search_logs 旧 BOOLEAN 列 DROP + tmp 列 RENAME

发布 30 天观察期通过后执行（对齐 README Runbook Step 6 / design Decision 10 第 4 条）。
此迁移 **不与 0005 一起执行**，保证 V1 回滚安全：
- upgrade：DROP 旧 BOOLEAN degraded/is_fallback 列 → RENAME tmp 列为 final 名 → 加命名 CHECK
- downgrade：反向 ADD 回 BOOLEAN 列 + 反 CASE WHEN 从 VARCHAR 回填 + RENAME tmp 列回 tmp 名

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-22

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 1. DROP 旧 BOOLEAN 列（0005 发布窗口内保留的 V1 rollback 写入目标） ===
    op.execute("ALTER TABLE kb_search_logs DROP COLUMN IF EXISTS degraded;")
    op.execute("ALTER TABLE kb_search_logs DROP COLUMN IF EXISTS is_fallback;")

    # === 2. RENAME tmp 列为 final 名 ===
    op.execute("ALTER TABLE kb_search_logs RENAME COLUMN degraded_tmp TO degraded;")
    op.execute("ALTER TABLE kb_search_logs RENAME COLUMN is_fallback_tmp TO is_fallback;")

    # === 3. 加命名 CHECK（与原设计 kb_search_logs_{col}_check 对齐） ===
    # tmp 列在 0005 已带内联 CHECK（随 RENAME 迁移到 final 名），此处补命名 CHECK 便于运维显式管理
    op.execute("""
        ALTER TABLE kb_search_logs ADD CONSTRAINT kb_search_logs_degraded_check
        CHECK (degraded IN ('0','1'));
    """)
    op.execute("""
        ALTER TABLE kb_search_logs ADD CONSTRAINT kb_search_logs_is_fallback_check
        CHECK (is_fallback IN ('0','1'));
    """)


def downgrade() -> None:
    # 反向回滚至 0005 状态：恢复旧 BOOLEAN 列 + tmp 列名
    # 1. 删除 0006 新增命名 CHECK（内联 CHECK 随 RENAME 回退自动复原）
    op.execute("ALTER TABLE kb_search_logs DROP CONSTRAINT IF EXISTS kb_search_logs_is_fallback_check;")
    op.execute("ALTER TABLE kb_search_logs DROP CONSTRAINT IF EXISTS kb_search_logs_degraded_check;")

    # 2. RENAME final 名回 tmp 名（恢复 0005 的双列过渡态）
    op.execute("ALTER TABLE kb_search_logs RENAME COLUMN is_fallback TO is_fallback_tmp;")
    op.execute("ALTER TABLE kb_search_logs RENAME COLUMN degraded TO degraded_tmp;")

    # 3. ADD 回旧 BOOLEAN 列 + 反 CASE WHEN 从 VARCHAR 回填
    op.execute("""
        ALTER TABLE kb_search_logs
        ADD COLUMN IF NOT EXISTS degraded BOOLEAN DEFAULT FALSE;
    """)
    op.execute("""
        ALTER TABLE kb_search_logs
        ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN DEFAULT FALSE;
    """)
    op.execute("""
        UPDATE kb_search_logs SET degraded = CASE WHEN degraded_tmp = '1' THEN TRUE ELSE FALSE END;
    """)
    op.execute("""
        UPDATE kb_search_logs SET is_fallback = CASE WHEN is_fallback_tmp = '1' THEN TRUE ELSE FALSE END;
    """)
