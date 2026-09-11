"""qc-service v2：拆 violations、扩展 qc_records、重建 qc_rules 为版本化包并种子 v1.0。

顺序：加新表 → 回填 JSONB violations → 废弃读写（本文件 MUST NOT DROP JSONB 列）。
MUST NOT 删除 qc_records.session_id REFERENCES call_records(session_id)。

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-10
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REPO = Path(__file__).resolve().parents[2]
_QC_ROOT = _REPO / "services" / "qc-service"
if str(_QC_ROOT) not in sys.path:
    sys.path.insert(0, str(_QC_ROOT))

from app.seeds.qc_rules_v1 import V1_SEED  # noqa: E402


def _sql_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("'", "''")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. qc_records：UNIQUE session_id（保留 FK）、扩展列；去掉复核列（已拆 qc_reviews）
    # ------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS idx_qc_records_session_id;")
    op.execute(
        """
        ALTER TABLE qc_records
            ADD CONSTRAINT uq_qc_records_session_id UNIQUE (session_id);
        """
    )
    op.execute(
        """
        ALTER TABLE qc_records
            ADD COLUMN IF NOT EXISTS case_id UUID REFERENCES cases(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS grade VARCHAR(20),
            ADD COLUMN IF NOT EXISTS qc_status VARCHAR(20) NOT NULL DEFAULT 'DONE',
            ADD COLUMN IF NOT EXISTS high_count INT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS med_count INT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS low_count INT NOT NULL DEFAULT 0;
        """
    )
    op.execute(
        """
        ALTER TABLE qc_records
            ADD CONSTRAINT qc_records_grade_check
            CHECK (grade IS NULL OR grade IN ('EXCELLENT','PASS','WARNING','SERIOUS'));
        """
    )
    op.execute(
        """
        ALTER TABLE qc_records
            ADD CONSTRAINT qc_records_qc_status_check
            CHECK (qc_status IN ('DONE','PENDING_REVIEW','REVIEWED','PARTIAL'));
        """
    )
    op.execute("ALTER TABLE qc_records DROP CONSTRAINT IF EXISTS qc_records_reviewer_id_fkey;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS reviewer_id;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS review_comment;")
    op.execute(
        """
        COMMENT ON COLUMN qc_records.session_id IS
        'REFERENCES call_records(session_id) 保留。WAIT_TEXT 只写 Redis、禁止为占坑 INSERT qc_records';
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN qc_records.risk_level IS
        '与 grade 独立：合并后存在 agent+high→high；否则累计扣分≥20→medium；否则 low';
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN qc_records.grade IS
        '由 compliance_score 与 grade_thresholds 决定：≥90 EXCELLENT / ≥80 PASS / ≥60 WARNING / <60 SERIOUS';
        """
    )

    # ------------------------------------------------------------------
    # 2. 先加新表（禁止与 DROP JSONB 同一步）
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE qc_violations (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            qc_record_id UUID NOT NULL REFERENCES qc_records(id) ON DELETE CASCADE,
            turn_id VARCHAR(50),
            rule_id VARCHAR(20),
            word_id UUID,
            severity VARCHAR(10) NOT NULL CHECK (severity IN ('high','medium','low')),
            redline_tag VARCHAR(30) CHECK (redline_tag IN (
                'threat','fake_identity','stop_call','fake_commit','legal_overstate',
                'unverified','data_fabrication','privacy_leak'
            )),
            speaker VARCHAR(10) NOT NULL CHECK (speaker IN ('agent','user')),
            evidence_text TEXT,
            start_offset_ms INT,
            end_offset_ms INT,
            hit_ts TIMESTAMP WITH TIME ZONE,
            create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
        );
        """
    )
    op.execute("CREATE INDEX idx_qc_violations_qc_record_id ON qc_violations(qc_record_id);")
    op.execute("CREATE INDEX idx_qc_violations_redline_tag ON qc_violations(redline_tag);")
    op.execute(
        """
        COMMENT ON COLUMN qc_violations.speaker IS
        '质检内部枚举 agent|user（≠ dialog_turns 的 ai|user）';
        """
    )

    op.execute(
        """
        CREATE TABLE qc_alerts (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            qc_record_id UUID NOT NULL REFERENCES qc_records(id) ON DELETE CASCADE,
            violation_id UUID REFERENCES qc_violations(id) ON DELETE SET NULL,
            level VARCHAR(10) NOT NULL CHECK (level IN ('high')),
            status VARCHAR(10) NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','ACKED')),
            acked_by UUID REFERENCES users(id) ON DELETE SET NULL,
            acked_at TIMESTAMP WITH TIME ZONE,
            create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
        );
        """
    )
    op.execute("CREATE INDEX idx_qc_alerts_qc_record_id ON qc_alerts(qc_record_id);")

    op.execute(
        """
        CREATE TABLE qc_reviews (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            qc_record_id UUID NOT NULL REFERENCES qc_records(id) ON DELETE CASCADE,
            reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
            action VARCHAR(20) NOT NULL CHECK (action IN ('CONFIRM','DISMISS','CORRECT')),
            revised_score INT CHECK (revised_score BETWEEN 0 AND 100),
            reason TEXT,
            create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
        );
        """
    )
    op.execute("CREATE INDEX idx_qc_reviews_qc_record_id ON qc_reviews(qc_record_id);")

    # ------------------------------------------------------------------
    # 3. 回填 JSONB violations → qc_violations（不 DROP JSONB）
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO qc_violations (
            qc_record_id, turn_id, rule_id, severity, redline_tag, speaker,
            evidence_text, start_offset_ms, end_offset_ms, hit_ts
        )
        SELECT
            r.id,
            NULLIF(elem->>'turn_id', ''),
            NULLIF(elem->>'rule_id', ''),
            COALESCE(NULLIF(elem->>'severity', ''), 'low'),
            CASE
                WHEN elem->>'redline_tag' IN (
                    'threat','fake_identity','stop_call','fake_commit','legal_overstate',
                    'unverified','data_fabrication','privacy_leak'
                ) THEN elem->>'redline_tag'
                ELSE NULL
            END,
            CASE WHEN elem->>'speaker' IN ('agent','user') THEN elem->>'speaker' ELSE 'agent' END,
            COALESCE(elem->>'evidence_text', elem->>'evidence'),
            NULLIF(elem->>'start_offset_ms', '')::int,
            NULLIF(elem->>'end_offset_ms', '')::int,
            CASE
                WHEN elem->>'hit_ts' IS NULL OR elem->>'hit_ts' = '' THEN NULL
                ELSE (elem->>'hit_ts')::timestamptz
            END
        FROM qc_records r
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(r.violations) = 'array' THEN r.violations ELSE '[]'::jsonb END
        ) AS elem
        WHERE r.violations IS NOT NULL;
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN qc_records.violations IS
        '过渡 JSONB；0011 已回填 qc_violations，废弃读写；本迁移禁止 DROP 本列';
        """
    )
    op.execute(
        """
        COMMENT ON TABLE qc_records IS
        '质检结果主表，合规分=100-Σ扣分，下限 0；session_id UNIQUE；JSONB violations 过渡期保留，权威明细见 qc_violations';
        """
    )

    # ------------------------------------------------------------------
    # 4. 重建 qc_rules 为版本化 JSONB 包 + 唯一 active 种子 v1.0
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE qc_rules RENAME TO qc_rules_row_legacy;")
    op.execute(
        """
        CREATE TABLE qc_rules (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            version VARCHAR(50) UNIQUE NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','archived')),
            rules JSONB NOT NULL,
            penalty JSONB DEFAULT jsonb_build_object('high', 20, 'medium', 8, 'low', 2),
            same_word_window_ms INTEGER DEFAULT 30000,
            grade_thresholds JSONB DEFAULT jsonb_build_object('EXCELLENT', 90, 'PASS', 80, 'WARNING', 60, 'SERIOUS', 0),
            effective_by UUID REFERENCES users(id) ON DELETE SET NULL,
            effective_at TIMESTAMP WITH TIME ZONE,
            description VARCHAR(255),
            create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
        );
        """
    )
    op.execute("CREATE UNIQUE INDEX idx_qc_rules_active ON qc_rules(status) WHERE status = 'active';")
    # D3：旧行式 qc_rules 数据丢弃，不以 keyword/regex 行回填；唯一 active 为 V1_SEED v1.0。
    op.execute("DROP TABLE IF EXISTS qc_rules_row_legacy;")

    rules_sql = _sql_json(V1_SEED["rules"])
    penalty_sql = _sql_json(V1_SEED["penalty"])
    grade_sql = _sql_json(V1_SEED["grade_thresholds"])
    desc_sql = str(V1_SEED["description"]).replace("'", "''")
    op.execute(
        f"""
        INSERT INTO qc_rules (
            version, status, rules, penalty, same_word_window_ms, grade_thresholds, description
        ) VALUES (
            '{V1_SEED["version"]}',
            'active',
            '{rules_sql}'::jsonb,
            '{penalty_sql}'::jsonb,
            {int(V1_SEED["same_word_window_ms"])},
            '{grade_sql}'::jsonb,
            '{desc_sql}'
        );
        """
    )
    op.execute(
        """
        COMMENT ON TABLE qc_rules IS
        '版本化质检规则配置表（R01~R15 规则包），DD-02 §6.1 / step4/01';
        """
    )
    op.execute(
        """
        COMMENT ON INDEX idx_qc_rules_active IS
        '全库仅保证一版 active';
        """
    )

    # ------------------------------------------------------------------
    # 5. 系统审计：skip 记录无操作用户（对齐 step4/01 COMMENT）
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE audit_logs ALTER COLUMN user_id DROP NOT NULL;")
    op.execute(
        """
        COMMENT ON COLUMN audit_logs.user_id IS
        '操作用户；系统触发（告警/定时任务/质检 skip）时为 NULL，具体行为体记 detail.actor';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE audit_logs ALTER COLUMN user_id SET NOT NULL;")
    op.execute("DROP TABLE IF EXISTS qc_reviews;")
    op.execute("DROP TABLE IF EXISTS qc_alerts;")
    op.execute("DROP TABLE IF EXISTS qc_violations;")
    op.execute("DROP TABLE IF EXISTS qc_rules;")
    op.execute(
        """
        CREATE TABLE qc_rules (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            rule_type VARCHAR(30) NOT NULL CHECK (rule_type IN ('keyword','regex','semantic','duration','silence')),
            pattern TEXT NOT NULL,
            severity VARCHAR(10) NOT NULL CHECK (severity IN ('high','medium','low')),
            score_deduct INT NOT NULL DEFAULT 10 CHECK (score_deduct BETWEEN 0 AND 100),
            status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
            description VARCHAR(255),
            create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
        );
        """
    )
    op.execute("CREATE INDEX idx_qc_rules_rule_type ON qc_rules(rule_type);")
    op.execute("CREATE INDEX idx_qc_rules_severity ON qc_rules(severity);")
    op.execute("ALTER TABLE qc_records DROP CONSTRAINT IF EXISTS qc_records_grade_check;")
    op.execute("ALTER TABLE qc_records DROP CONSTRAINT IF EXISTS qc_records_qc_status_check;")
    op.execute("ALTER TABLE qc_records DROP CONSTRAINT IF EXISTS uq_qc_records_session_id;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS case_id;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS agent_id;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS grade;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS qc_status;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS high_count;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS med_count;")
    op.execute("ALTER TABLE qc_records DROP COLUMN IF EXISTS low_count;")
    op.execute(
        """
        ALTER TABLE qc_records
            ADD COLUMN IF NOT EXISTS reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS review_comment TEXT;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_qc_records_session_id ON qc_records(session_id);")
    op.execute(
        """
        COMMENT ON COLUMN qc_records.violations IS '质检违规明细 JSONB';
        """
    )
