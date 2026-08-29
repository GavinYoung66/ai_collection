"""kb_doc_assets 资产表 + kb_search_logs LLM 回答标记（V5 多模态资产预览载体）

RAG 多模态知识库 V5：
- 新增 kb_doc_assets：文档内嵌资产（图片/表格）元数据表，作为 Gradio 结构化预览
  载体（表格 content_json 供 gr.Dataframe 渲染；图片 storage_path 供共享 volume 读取）。
- kb_search_logs 增列 llm_called / llm_degraded：LLM 回答调用与降级标记，
  VARCHAR(1)+CHECK 对齐全局类型规范（同 0005 degraded_tmp 模式），默认 '0' 不强制回填。

Revision ID: 0008（= 既有最大 revision 0007 的下一个）
Revises: 0007
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 1. 新增 kb_doc_assets 资产表 ===
    op.execute("""
    CREATE TABLE kb_doc_assets (
        id BIGSERIAL PRIMARY KEY,
        doc_id UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
        asset_type VARCHAR(10) NOT NULL CHECK (asset_type IN ('image','table')),
        page INT NOT NULL DEFAULT 0,
        storage_path VARCHAR(500) NOT NULL,
        ocr_text TEXT,
        mime_type VARCHAR(100),
        content_json TEXT,
        create_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_kb_doc_assets_doc ON kb_doc_assets(doc_id);")
    op.execute("CREATE INDEX idx_kb_doc_assets_doc_type ON kb_doc_assets(doc_id, asset_type);")
    op.execute("COMMENT ON TABLE kb_doc_assets IS '知识库文档内嵌资产表（图片/表格，结构化预览载体）';")

    # === 2. kb_search_logs 增 LLM 回答标记（默认 '0'，存量行不强制回填） ===
    op.execute("""
        ALTER TABLE kb_search_logs
        ADD COLUMN IF NOT EXISTS llm_called VARCHAR(1) NOT NULL DEFAULT '0'
        CHECK (llm_called IN ('0','1'));
    """)
    op.execute("""
        ALTER TABLE kb_search_logs
        ADD COLUMN IF NOT EXISTS llm_degraded VARCHAR(1) NOT NULL DEFAULT '0'
        CHECK (llm_degraded IN ('0','1'));
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE kb_search_logs DROP COLUMN IF EXISTS llm_degraded;")
    op.execute("ALTER TABLE kb_search_logs DROP COLUMN IF EXISTS llm_called;")
    op.execute("DROP INDEX IF EXISTS idx_kb_doc_assets_doc_type;")
    op.execute("DROP INDEX IF EXISTS idx_kb_doc_assets_doc;")
    op.execute("DROP TABLE IF EXISTS kb_doc_assets;")
