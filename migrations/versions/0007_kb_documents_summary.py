"""kb_documents 增列 summary（V4 摘要元数据，权威源，可空）

RAG 多模态知识库 V4：摘要元数据能力。摘要由上游业务中台预生成，经
上传携带或独立补全接口注入，RAG-Service 仅存储/索引/检索返回（PRD §1.4 边界）。
- kb_documents.summary TEXT NULL：文档级摘要权威源，存量行不强制回填；
  ES kb_bm25.doc_summary 为检索副本（重建见 scripts/rag/reindex_es_v4.py）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 文档级摘要（可空、不强制回填）；权威源在 PG，检索副本在 ES doc_summary
    op.execute("ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS summary TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE kb_documents DROP COLUMN IF EXISTS summary;")
