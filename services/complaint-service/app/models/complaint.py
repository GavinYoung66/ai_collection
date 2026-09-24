"""Complaint ORM——映射已有 complaints 表，并扩展 PRD 9.2 字段。

设计决策：仓库内无现有 Complaint ORM 模型，complaints 表仅在
0001_init_tables.py 以 raw SQL 创建。此处新建 ORM 映射已有表，
不重复建表；扩展字段通过 0010 迁移 ALTER 添加。
"""
from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from common.db.base import Base, PublicFieldsMixin

# ── verdict 四值枚举（禁用 normal） ──
_VERDICTS = (
    "agent_violation",
    "user_misunderstanding",
    "compliant_but_poor_experience",
    "pending",
)

# ── status 合法值（保持 0001 既有定义） ──
_STATUSES = ("pending", "investigating", "resolved", "closed")


class Complaint(Base, PublicFieldsMixin):
    """投诉工单表（映射已有 complaints，扩展判责字段）。"""

    __tablename__ = "complaints"

    # 注意：cases / call_records 仅有 0001 raw SQL 建表、无同 metadata ORM 模型，
    # 此处不能声明 ForeignKey("cases.id")，否则 mapper configure 找不到目标表。
    # 外键约束以数据库 0001 迁移为准（cases.id ON DELETE CASCADE 等），ORM 只做列映射。
    case_id = Column(UUID(as_uuid=True), nullable=False)
    session_id = Column(String(100))
    complaint_text = Column(Text, nullable=False)
    verdict = Column(String(30))
    evidence = Column(JSONB)
    suggestion = Column(Text)
    status = Column(String(20), nullable=False, default="pending")

    # ── PRD 9.2 扩展字段 ──
    verdict_reasoning = Column(Text)
    response_draft = Column(Text)
    regulation_refs = Column(JSONB, default=list)
    confidence = Column(Float)
    internal_improvement = Column(Text)
    conflict_highlight = Column(Boolean, default=False)

    __table_args__ = (
        CheckConstraint(
            f"status IN ({','.join(repr(s) for s in _STATUSES)})",
            name="ck_complaints_status",
        ),
        CheckConstraint(
            f"verdict IN ({','.join(repr(v) for v in _VERDICTS)})",
            name="ck_complaints_verdict",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_complaints_confidence",
        ),
    )
