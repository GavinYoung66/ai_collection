"""SQLAlchemy ORM 模型：kb_documents / kb_chunks / kb_search_logs / sensitive_words / kb_doc_assets。"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, Column, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base import Base, PublicFieldsMixin


class KBDocument(Base, PublicFieldsMixin):
    __tablename__ = "kb_documents"
    __table_args__ = {"comment": "知识库文档主表"}

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="processing", server_default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # V2 新增元数据列（存量 V1 文档允许 NULL）
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    call_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    risk_tag: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "ocr"/"asr"/"v1_legacy"/null
    # V4 新增：摘要元数据（文档级，上游预生成；PG 权威源，ES kb_bm25.doc_summary 为检索副本）
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 异步入库可观测（async-kb-ingest-observability）
    ingest_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ingest_progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingest_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KBChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        {"comment": "知识库分块表，关联 Milvus 向量和 ES 索引"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    vector_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    category: Mapped[str | None] = mapped_column(String(50), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    update_time = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    is_delete: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")


class KBSearchLog(Base):
    __tablename__ = "kb_search_logs"
    __table_args__ = (
        {"comment": "RAG 检索全链路日志（V5 增 LLM 回答调用/降级标记）"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))
    dense_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    sparse_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    fused_count: Mapped[int] = mapped_column(Integer, default=0)
    reranked_count: Mapped[int] = mapped_column(Integer, default=0)
    max_rerank_score: Mapped[float | None] = mapped_column(Float)
    hit_doc_ids: Mapped[str | None] = mapped_column(Text)
    hit_chunk_ids: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # V2: BOOLEAN → VARCHAR('0'/'1')，迁移 0005 完成后类型为 VARCHAR(1)
    degraded: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")
    is_fallback: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")
    cache_hit: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")  # '0'/'1'，V1-T20
    # V5: LLM 回答调用/降级标记（迁移 0008；默认 '0'，存量行不强制回填）
    llm_called: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")  # '0'/'1'
    llm_degraded: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")  # '0'/'1'
    # V2 新增 5 字段（迁移 0005）
    query_original: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    query_rewritten: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    intent_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_delete: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")


class SensitiveWord(Base, PublicFieldsMixin):
    __tablename__ = "sensitive_words"
    __table_args__ = {"comment": "敏感词词库，QC 与 AI 输出拦截共用，命中即 B0010 拦截"}

    word: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # threat/abuse/induce
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # high/medium/low
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active/disabled
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class KbDocAsset(Base):
    """V5：文档内嵌资产元数据表（图片/表格），Gradio 结构化预览载体。

    与迁移 0008 对齐：id BIGSERIAL PK / doc_id UUID FK kb_documents ON DELETE CASCADE /
    asset_type image|table / storage_path 供共享 volume 读取 / content_json 表格行列
    结构化 JSON（图片为 NULL）/ 公共字段 create_time, update_time, is_delete。
    """

    __tablename__ = "kb_doc_assets"
    __table_args__ = (
        Index("idx_kb_doc_assets_doc", "doc_id"),
        Index("idx_kb_doc_assets_doc_type", "doc_id", "asset_type"),
        {"comment": "知识库文档内嵌资产表（图片/表格，结构化预览载体）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 'image'/'table'
    page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 表格行列结构化 JSON，图片为 NULL
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    update_time = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    is_delete: Mapped[str] = mapped_column(CHAR(1), nullable=False, default="0")
