"""rag-service v1: extend kb_documents.status CHECK + create kb_search_logs

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_status_check;
    """)
    op.execute("""
        ALTER TABLE kb_documents ADD CONSTRAINT kb_documents_status_check
        CHECK (status IN ('processing','active','archived','failed'));
    """)

    op.execute("""
    CREATE TABLE kb_search_logs (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        trace_id VARCHAR(64) NOT NULL,
        query TEXT NOT NULL,
        category VARCHAR(50),
        dense_hit_count INT DEFAULT 0,
        sparse_hit_count INT DEFAULT 0,
        fused_count INT DEFAULT 0,
        reranked_count INT DEFAULT 0,
        max_rerank_score FLOAT,
        hit_doc_ids TEXT,
        hit_chunk_ids TEXT,
        latency_ms INT,
        degraded BOOLEAN DEFAULT FALSE,
        is_fallback BOOLEAN DEFAULT FALSE,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_kb_search_logs_trace ON kb_search_logs(trace_id);")
    op.execute("CREATE INDEX idx_kb_search_logs_created ON kb_search_logs(create_time DESC);")
    op.execute("COMMENT ON TABLE kb_search_logs IS 'RAG 检索全链路日志，append-only，用于问题复现和 BadCase 排查';")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kb_search_logs;")
    op.execute("""
        ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_status_check;
    """)
    op.execute("""
        ALTER TABLE kb_documents ADD CONSTRAINT kb_documents_status_check
        CHECK (status IN ('active','archived'));
    """)
