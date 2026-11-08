"""kb_documents 同名部分唯一索引：active/processing 收敛（r3 T119）

背景（问题清单-2026-11-05-r3 T119）：kb_documents.title 无唯一索引，rag 上传路径
check-then-act（DocRepo.find_processing_by_title / find_active_by_title 先查后插，
documents.py upload_document）在并发同名上传下存在竞态窗口，可产生双 processing
记录且无自愈。

本迁移新增部分唯一索引：仅约束 active/processing 且未删除的行——与 check-then-act
两查询的判重口径（仅按 title、不分 category）及迁移 0002 放宽后的 status CHECK
（processing/active/archived/failed）完全同口径；failed 行与已删除行（archived +
is_delete='1'）不占用同名名额，不阻塞重新上传。应用层配套：上传路径捕获
IntegrityError（pgcode 23505）rollback 后自愈查询既有行，转 B0001 明确冲突。

回滚策略（design Migration Plan 5）：本变更为代码级整改，回滚代码时保留索引无
副作用（既有数据在唯一约束下语义不变）；downgrade 提供标准 DROP INDEX 形态。

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "uq_kb_documents_active_title"


def _assert_no_duplicate_active_titles() -> None:
    """建索引前预检存量重复行：存在同名 active/processing 时明确报错。

    策略（2026-11-08 审核补充）：不自动归档/合并业务数据，只 fail-fast 并列出
    冲突 title 与条数，交由运维人工裁决后再重跑迁移——避免原始索引错误信息
    难以定位，也避免迁移脚本隐式改数据。
    """
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT title, count(*) AS n FROM kb_documents "
            "WHERE status IN ('active','processing') AND is_delete = '0' "
            "GROUP BY title HAVING count(*) > 1 ORDER BY n DESC, title LIMIT 10"
        )
    ).fetchall()
    if rows:
        detail = "；".join(f"title={row[0]!r}×{row[1]}" for row in rows)
        raise RuntimeError(
            "0019 迁移中止：kb_documents 存在同名 active/processing 重复行，"
            "请先人工裁决（归档/删除多余行）后重跑迁移。冲突样例："
            f"{detail}"
        )


def upgrade() -> None:
    # 离线（--sql）模式无连接，跳过预检仅输出 DDL。
    if not context.is_offline_mode():
        _assert_no_duplicate_active_titles()
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME} "
        "ON kb_documents(title) "
        "WHERE status IN ('active','processing') AND is_delete = '0';"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME};")