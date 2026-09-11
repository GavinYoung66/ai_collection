"""ComplaintAgentLog ORM——Agent 执行逐步日志（append-only，全链路可审计可回溯）。

字段对齐 PRD 9.1 DDL。append-only：无 update_time / is_delete。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from common.db.base import Base

# ── agent_role 合法值 ──
_AGENT_ROLES = (
    "orchestrator",
    "evidence_case",
    "evidence_call_qc",
    "reflection",
    "fact",
    "compliance",
    "user_demand",
    "verdict",
    "generate_package",
    "sample_backflow",
)

# ── status 合法值 ──
_STATUSES = ("success", "failed", "degraded", "skipped")


class ComplaintAgentLog(Base):
    """Agent 执行日志表，append-only。"""

    __tablename__ = "complaint_agent_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        String(100),
        ForeignKey("complaint_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id = Column(String(100), nullable=False)
    agent_role = Column(String(50), nullable=False)
    step_name = Column(String(100), nullable=False)
    input_summary = Column(Text)
    output_json = Column(JSONB)
    mcp_calls = Column(JSONB, default=list)
    llm_call_log_ids = Column(JSONB, default=list)
    tokens_consumed = Column(Integer, default=0)
    latency_ms = Column(Integer)
    status = Column(String(20), nullable=False)
    error_msg = Column(Text)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"agent_role IN ({','.join(repr(r) for r in _AGENT_ROLES)})",
            name="ck_complaint_agent_logs_agent_role",
        ),
        CheckConstraint(
            f"status IN ({','.join(repr(s) for s in _STATUSES)})",
            name="ck_complaint_agent_logs_status",
        ),
    )
