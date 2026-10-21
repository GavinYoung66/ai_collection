"""ComplaintSession ORM——投诉研判 LangGraph 会话表，存储完整 Agent 编排全量数据。

字段对齐 PRD 9.1 DDL 与 6.1 State Schema。
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from common.db.base import Base, PublicFieldsMixin

# ── task_status 合法值（须与 app/models/state.py 的 TaskStatus 逐字一致） ──
# 与迁移 0015 的 CHECK 约束同源；新增状态时三处（此处 / state.py / 新迁移）必须同改。
_TASK_STATUSES = (
    "pending",
    "evidence_gathering",
    "reflection_retry",
    "judging",
    "draft_generated",
    "pending_review",
    "pending_senior_review",
    "pending_callback",  # V4 定责完成、待回电处置
    "resolved",
    "closed",
    "archived",
)


class ComplaintSession(Base, PublicFieldsMixin):
    """投诉研判会话表。

    权威边界（重要）：
    - **状态机列**（task_status / updated_at / status_changed_at）由
      SessionService.transition 唯一写入，本表是这部分数据的权威来源。
    - **图产出列**（case_evidence / verdict_draft / judgment_package 等 JSONB）
      仅作只读快照，**不得**用于反推 LangGraph 的 resume 状态。
      图执行态（含 interrupt 断点、pending_writes）的唯一权威是 LangGraph
      的 checkpoints / checkpoint_blobs / checkpoint_writes 三表。
    """

    __tablename__ = "complaint_sessions"

    session_id = Column(String(100), unique=True, nullable=False)
    trace_id = Column(String(100), nullable=False)
    complaint_id = Column(
        UUID(as_uuid=True),
        ForeignKey("complaints.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ── 关联 ID 列表（支持一对多） ──
    case_ids = Column(JSONB, nullable=False, default=list)
    session_ids = Column(JSONB, nullable=False, default=list)
    user_id = Column(String(100))
    complaint_text = Column(Text, nullable=False)
    # ── 反思循环控制 ──
    loop_count = Column(Integer, nullable=False, default=0)
    max_loops = Column(Integer, nullable=False, default=3)
    # ── 证据数据（JSONB，对齐 State Schema） ──
    case_evidence = Column(JSONB, default=dict)  # {case_id: CaseEvidenceBundle}
    cross_case_evidence = Column(JSONB, default=list)
    historical_complaint_evidence = Column(JSONB, default=list)
    # ── Agent 中间输出 ──
    reflection_report = Column(JSONB)
    fact_timeline = Column(JSONB)
    compliance_opinion = Column(JSONB)
    user_demand = Column(JSONB)
    verdict_draft = Column(JSONB)
    judgment_package = Column(JSONB)
    # ── 异常与人工 ──
    exception_info = Column(JSONB, default=dict)
    human_review = Column(JSONB)
    # ── 状态与模式 ──
    task_status = Column(String(30), nullable=False, default="pending")
    total_tokens_consumed = Column(Integer, default=0)
    max_token_threshold = Column(Integer, default=50000)
    mode = Column(String(20), nullable=False, default="multi_agent")

    __table_args__ = (
        CheckConstraint("loop_count BETWEEN 0 AND 3", name="ck_complaint_sessions_loop_count"),
        CheckConstraint(
            f"task_status IN ({','.join(repr(s) for s in _TASK_STATUSES)})",
            name="ck_complaint_sessions_task_status",
        ),
        CheckConstraint(
            "mode IN ('single_agent','multi_agent')",
            name="ck_complaint_sessions_mode",
        ),
    )
