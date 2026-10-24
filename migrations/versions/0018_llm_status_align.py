"""对齐 llm_call_logs.status CHECK 与实际写入值（f3-usage-console 真链路）。

背景（P1-1 实证）：表约束只允许计划四值 success/failed/timeout/circuit_open，
但 qc-service 实际写入 ok / qc_review_exemption / skipped，导致每次 INSERT 都
触发 CheckViolationError，llm_call_logs 长期为空 —— 用量看板无真实数据可读。

本迁移保留旧四值（历史兼容、其他写入方可能使用），并补入实际三值。

downgrade 恢复原四值约束；若已存在实际三值行，降级会失败（需先清理数据）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "status IN ('success','failed','timeout','circuit_open')"
_NEW = (
    "status IN ('success','failed','timeout','circuit_open',"
    "'ok','qc_review_exemption','skipped')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE llm_call_logs DROP CONSTRAINT IF EXISTS llm_call_logs_status_check;")
    op.execute(f"ALTER TABLE llm_call_logs ADD CONSTRAINT llm_call_logs_status_check CHECK ({_NEW});")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_call_logs DROP CONSTRAINT IF EXISTS llm_call_logs_status_check;")
    op.execute(f"ALTER TABLE llm_call_logs ADD CONSTRAINT llm_call_logs_status_check CHECK ({_OLD});")
