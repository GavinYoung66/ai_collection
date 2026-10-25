"""llm_call_logs 表 ORM 模型（append-only，宪法强制）。"""
import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from common.db.base import Base


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"
    __table_args__ = {"comment": "LLM 调用全量审计表，强制落库；confidence<0.7 禁止直接回复"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id = Column(String(100), nullable=False, index=True)
    session_id = Column(String(100), nullable=True, index=True)
    provider = Column(String(50), nullable=False)
    model = Column(String(50), nullable=True)
    svc = Column(
        String(50),
        nullable=True,
        index=True,
        comment="上报服务名（qc/rag/complaint）；NULL 为本列落地前的历史行",
    )
    prompt = Column(Text, nullable=False, comment="入参，落库前经 mask.py 脱敏")
    response = Column(Text, nullable=True)
    token_usage = Column(JSONB, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=True)
    temperature = Column(Float, default=0.1, server_default="0.1")
    status = Column(
        String(20),
        nullable=False,
        index=True,
        comment="success/failed/timeout/circuit_open（计划值）+ ok/qc_review_exemption/skipped（实际写入）",
    )
    error_msg = Column(Text, nullable=True)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
