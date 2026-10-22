"""qc-deduction-scoring：四表加扣分口径列 + 近似回填。

- qc_records    +deduction_total INT NOT NULL DEFAULT 0 CHECK (>=0)
- qc_violations +points INT NOT NULL DEFAULT 0 CHECK (>=0)
                 +policy_clause VARCHAR(100) / +policy_text TEXT
- qc_reviews    +revised_deduction INT CHECK (>=0)
- qc_rules      +deduction_policy JSONB

回填（有损近似，design.md D8 显式声明）：
- qc_records.deduction_total = 100 - compliance_score；
  compliance_score=0 的历史行实际扣分已被 max(0,·) 截断不可恢复，回填 100。
- qc_violations.points = penalty[severity]（high20/medium8/low2），
  仅 score_eligible 行（库内口径 = speaker='agent'）；user 行保持默认 0。
- qc_reviews.revised_deduction = 100 - revised_score；revised_score 为 NULL 保持 NULL。

downgrade 逐列 DROP，可逆。

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-15

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === qc_records +deduction_total ===
    op.execute(
        "ALTER TABLE qc_records "
        "ADD COLUMN IF NOT EXISTS deduction_total INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE qc_records "
        "ADD CONSTRAINT qc_records_deduction_total_check CHECK (deduction_total >= 0)"
    )
    op.execute(
        "COMMENT ON COLUMN qc_records.deduction_total IS "
        "'扣分合计（主口径）；compliance_score=max(0,100-deduction_total) 派生'"
    )

    # === qc_violations +points / policy_clause / policy_text ===
    op.execute(
        "ALTER TABLE qc_violations "
        "ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE qc_violations "
        "ADD CONSTRAINT qc_violations_points_check CHECK (points >= 0)"
    )
    op.execute(
        "ALTER TABLE qc_violations ADD COLUMN IF NOT EXISTS policy_clause VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE qc_violations ADD COLUMN IF NOT EXISTS policy_text TEXT"
    )
    op.execute(
        "COMMENT ON COLUMN qc_violations.points IS '本条扣分（severity→penalty 推出）'"
    )
    op.execute(
        "COMMENT ON COLUMN qc_violations.policy_clause IS '制度条款号（静态表填充）'"
    )
    op.execute(
        "COMMENT ON COLUMN qc_violations.policy_text IS '制度条款原文（静态表填充）'"
    )

    # === qc_reviews +revised_deduction ===
    op.execute(
        "ALTER TABLE qc_reviews ADD COLUMN IF NOT EXISTS revised_deduction INTEGER"
    )
    op.execute(
        "ALTER TABLE qc_reviews "
        "ADD CONSTRAINT qc_reviews_revised_deduction_check "
        "CHECK (revised_deduction IS NULL OR revised_deduction >= 0)"
    )
    op.execute(
        "COMMENT ON COLUMN qc_reviews.revised_deduction IS "
        "'人工改判扣分；revised_score=max(0,100-revised_deduction) 派生'"
    )

    # === qc_rules +deduction_policy ===
    op.execute("ALTER TABLE qc_rules ADD COLUMN IF NOT EXISTS deduction_policy JSONB")
    op.execute(
        "COMMENT ON COLUMN qc_rules.deduction_policy IS "
        "'rule_id→{clause_no,clause_text} 静态制度条款映射，含 __ac_default__ 兜底'"
    )

    # === 回填（有损近似：design D8 显式声明）===
    # compliance_score=0 的旧行实际扣分已被 max(0,·) 截断不可恢复，回填 100；
    # PARTIAL 行 deduction_total=0 取 DEFAULT；新数据无损。
    op.execute("UPDATE qc_records SET deduction_total = 100 - compliance_score")
    op.execute(
        """
        UPDATE qc_violations SET points = CASE severity
            WHEN 'high' THEN 20
            WHEN 'medium' THEN 8
            WHEN 'low' THEN 2
            ELSE 0 END
        WHERE speaker = 'agent';
        """
    )
    op.execute(
        "UPDATE qc_reviews SET revised_deduction = 100 - revised_score "
        "WHERE revised_score IS NOT NULL"
    )

    # === 刷新存量 v1.0 规则包：补 deduction_policy，清空已退役的 grade_thresholds ===
    # 与 0011 同样的种子内嵌模式（from-scratch 回放时 0011 插入的包不含
    # deduction_policy 列——该列由本迁移创建，故此处统一刷新一次收敛终态）。
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    _svc_app = _Path(__file__).resolve().parents[2] / "services" / "qc-service"
    if str(_svc_app) not in _sys.path:
        _sys.path.insert(0, str(_svc_app))
    from app.seeds.qc_rules_v1 import V1_SEED  # noqa: E402

    _policy_sql = _json.dumps(V1_SEED["deduction_policy"], ensure_ascii=False).replace("'", "''")
    op.execute(
        f"""
        UPDATE qc_rules
        SET deduction_policy = '{_policy_sql}'::jsonb,
            grade_thresholds = NULL
        WHERE version = '{V1_SEED["version"]}';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE qc_rules DROP COLUMN IF EXISTS deduction_policy")

    op.execute(
        "ALTER TABLE qc_reviews "
        "DROP CONSTRAINT IF EXISTS qc_reviews_revised_deduction_check"
    )
    op.execute("ALTER TABLE qc_reviews DROP COLUMN IF EXISTS revised_deduction")

    op.execute(
        "ALTER TABLE qc_violations "
        "DROP CONSTRAINT IF EXISTS qc_violations_points_check"
    )
    op.execute("ALTER TABLE qc_violations DROP COLUMN IF EXISTS policy_text")
    op.execute("ALTER TABLE qc_violations DROP COLUMN IF EXISTS policy_clause")
    op.execute("ALTER TABLE qc_violations DROP COLUMN IF EXISTS points")

    op.execute(
        "ALTER TABLE qc_records "
        "DROP CONSTRAINT IF EXISTS qc_records_deduction_total_check"
    )
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS deduction_total")
