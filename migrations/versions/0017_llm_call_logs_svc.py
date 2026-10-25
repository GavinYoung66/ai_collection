"""llm_call_logs 增加 svc 列（f3-usage-console 真链路接入）。

背景：用量看板需按「服务」维度聚合（summary.by_service），但 llm_call_logs
历史无服务标识列。本迁移补一列 nullable，由各写入方填自身服务名
（当前唯一 ORM 写入方 qc-service 填 "qc"）。

- ADD COLUMN svc VARCHAR(50) NULL
- CREATE INDEX idx_llm_call_logs_svc_create_time (svc, create_time DESC)
- COMMENT 说明口径

downgrade 可逆（DROP INDEX + DROP COLUMN）。

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-24

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_call_logs ADD COLUMN svc VARCHAR(50) NULL;")
    op.execute(
        "CREATE INDEX idx_llm_call_logs_svc_create_time "
        "ON llm_call_logs(svc, create_time DESC);"
    )
    op.execute(
        "COMMENT ON COLUMN llm_call_logs.svc IS "
        "'上报服务名（qc/rag/complaint）；NULL 表示本列落地前的历史行';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_call_logs_svc_create_time;")
    op.execute("ALTER TABLE llm_call_logs DROP COLUMN IF EXISTS svc;")
