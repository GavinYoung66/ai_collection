"""kb_documents 增列入库可观测字段（async-kb-ingest-observability）

- ingest_stage: parse|body_index|asset_index|done
- ingest_progress: 0-100 离散进度
- ingest_error: 失败可读原因

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-02

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS ingest_stage VARCHAR(32);"
    )
    op.execute(
        "ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS ingest_progress INTEGER;"
    )
    op.execute(
        "ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS ingest_error TEXT;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE kb_documents DROP COLUMN IF EXISTS ingest_error;")
    op.execute("ALTER TABLE kb_documents DROP COLUMN IF EXISTS ingest_progress;")
    op.execute("ALTER TABLE kb_documents DROP COLUMN IF EXISTS ingest_stage;")
