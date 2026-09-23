"""data-platform 最小闭环：dw.ads_case_loop 案件快照表（14 列，一行一 case_id）。

仅一张极简快照，不建 ODS/DWD/DWS、不建第二张宽表、不加报表列。
质检列名沿用 qc_records（compliance_score/risk_level），禁止 score/risk。
call_ended / has_high_alert 为 char(1)，禁止 boolean。

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # dw schema 已在 0001 创建；IF NOT EXISTS 保持幂等。
    op.execute("CREATE SCHEMA IF NOT EXISTS dw;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dw.ads_case_loop (
            case_id          VARCHAR(36) NOT NULL,
            customer_name    VARCHAR(100),
            overdue_amount   DECIMAL(12,2),
            overdue_days     INT,
            session_id       VARCHAR(100),
            call_ended       CHAR(1) CHECK (call_ended IS NULL OR call_ended IN ('0','1')),
            qc_record_id     VARCHAR(36),
            compliance_score INT CHECK (compliance_score IS NULL OR compliance_score BETWEEN 0 AND 100),
            risk_level       VARCHAR(10) CHECK (risk_level IS NULL OR risk_level IN ('low','medium','high')),
            has_high_alert   CHAR(1) CHECK (has_high_alert IS NULL OR has_high_alert IN ('0','1')),
            complaint_id     VARCHAR(36),
            verdict          VARCHAR(30),
            task_status      VARCHAR(20),
            backflow_at      TIMESTAMP WITH TIME ZONE
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ads_case_loop_case_id ON dw.ads_case_loop(case_id);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ads_case_loop_session_id ON dw.ads_case_loop(session_id);")
    op.execute(
        """
        COMMENT ON TABLE dw.ads_case_loop IS
        'data-platform 最小闭环案件快照（14 列，一行一 case_id）；'
        '质检列名沿用 qc_records.compliance_score/risk_level，call_ended/has_high_alert 为 char(1)';
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN dw.ads_case_loop.case_id IS
        '案件主键字符串 = public.cases.id（UUID），全链路四处对得上的同一 case_id';
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN dw.ads_case_loop.call_ended IS
        '通话是否结束，char(1) 仅允许 0/1，禁止 boolean';
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN dw.ads_case_loop.has_high_alert IS
        '该 session_id 的 qc_alerts 是否存在 level=high：1/0，char(1)，禁止 boolean';
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN dw.ads_case_loop.backflow_at IS
        '最后回流时间，按 Asia/Shanghai 写入';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dw.ads_case_loop;")
