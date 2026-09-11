"""质检五表 ORM（step4/01）。库列不得改名为 score/risk；无 quality_id、无 skipped_rules 列。"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.models.fk_targets import register_fk_targets
from common.db.base import Base, PublicFieldsMixin

register_fk_targets()

QC_RESULT_VALUES = ("compliant", "violation", "pending_review")
QC_STATUS_VALUES = ("DONE", "PENDING_REVIEW", "REVIEWED", "PARTIAL")
GRADE_VALUES = ("EXCELLENT", "PASS", "WARNING", "SERIOUS")
RISK_LEVEL_VALUES = ("low", "medium", "high")
# step4/01 CHECK；AC 词类 induce/abuse 不在列内，落库须置空，禁止改 DDL。
REDLINE_TAG_VALUES = (
    "threat",
    "fake_identity",
    "stop_call",
    "fake_commit",
    "legal_overstate",
    "unverified",
    "data_fabrication",
    "privacy_leak",
)
_FORBIDDEN_QC_STATUS = frozenset({"WAIT_TEXT", "FAILED", "RUNNING"})


class QcRecord(Base, PublicFieldsMixin):
    __tablename__ = "qc_records"
    __table_args__ = (
        CheckConstraint("compliance_score BETWEEN 0 AND 100", name="qc_records_compliance_score_check"),
        CheckConstraint("risk_level IN ('low','medium','high')", name="qc_records_risk_level_check"),
        CheckConstraint(
            "qc_result IN ('compliant','violation','pending_review')",
            name="qc_records_qc_result_check",
        ),
        CheckConstraint(
            "grade IS NULL OR grade IN ('EXCELLENT','PASS','WARNING','SERIOUS')",
            name="qc_records_grade_check",
        ),
        CheckConstraint(
            "qc_status IN ('DONE','PENDING_REVIEW','REVIEWED','PARTIAL')",
            name="qc_records_qc_status_check",
        ),
        CheckConstraint("is_delete IN ('0','1')", name="qc_records_is_delete_check"),
        Index("idx_qc_records_qc_result", "qc_result"),
        Index("idx_qc_records_qc_result_create_time", "qc_result", "create_time"),
        Index(
            "idx_qc_records_pending_review",
            "create_time",
            postgresql_ops={"create_time": "DESC"},
            postgresql_where=text("qc_result = 'pending_review' AND is_delete = '0'"),
        ),
        {"comment": "质检结果主表；JSONB violations 过渡期保留，权威明细见 qc_violations"},
    )

    session_id = Column(
        String(100),
        ForeignKey("call_records.session_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="REFERENCES call_records(session_id) 保留；WAIT_TEXT 禁止占坑 INSERT",
    )
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    compliance_score = Column(Integer, nullable=False)
    risk_level = Column(String(10), nullable=False)
    qc_result = Column(String(20), nullable=False)
    grade = Column(String(20), nullable=True)
    qc_status = Column(String(20), nullable=False, default="DONE", server_default="DONE")
    high_count = Column(Integer, nullable=False, default=0, server_default="0")
    med_count = Column(Integer, nullable=False, default=0, server_default="0")
    low_count = Column(Integer, nullable=False, default=0, server_default="0")
    violations = Column(
        JSONB,
        nullable=True,
        comment="过渡 JSONB；0011 回填 qc_violations 后废弃读写，禁止 DROP",
    )

    violation_rows = relationship("QcViolation", back_populates="record", cascade="all, delete-orphan")
    alerts = relationship("QcAlert", back_populates="record", cascade="all, delete-orphan")
    reviews = relationship("QcReview", back_populates="record", cascade="all, delete-orphan")

    @property
    def qc_record_id(self) -> uuid.UUID:
        """对外主键名 = qc_records.id。"""
        return self.id

    @staticmethod
    def assert_db_status(qc_status: str) -> str:
        if qc_status in _FORBIDDEN_QC_STATUS or qc_status not in QC_STATUS_VALUES:
            raise ValueError(f"qc_status must be one of {QC_STATUS_VALUES}, got {qc_status!r}")
        return qc_status


class QcViolation(Base, PublicFieldsMixin):
    __tablename__ = "qc_violations"
    __table_args__ = (
        CheckConstraint("severity IN ('high','medium','low')", name="qc_violations_severity_check"),
        CheckConstraint(
            "redline_tag IS NULL OR redline_tag IN "
            "('threat','fake_identity','stop_call','fake_commit','legal_overstate',"
            "'unverified','data_fabrication','privacy_leak')",
            name="qc_violations_redline_tag_check",
        ),
        CheckConstraint("speaker IN ('agent','user')", name="qc_violations_speaker_check"),
        CheckConstraint("is_delete IN ('0','1')", name="qc_violations_is_delete_check"),
        Index("idx_qc_violations_qc_record_id", "qc_record_id"),
        Index("idx_qc_violations_redline_tag", "redline_tag"),
        {"comment": "违规明细（含 redline_tag）"},
    )

    qc_record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("qc_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_id = Column(String(50), nullable=True)
    rule_id = Column(String(20), nullable=True)
    word_id = Column(UUID(as_uuid=True), nullable=True)
    severity = Column(String(10), nullable=False)
    redline_tag = Column(String(30), nullable=True)
    speaker = Column(String(10), nullable=False)
    evidence_text = Column(Text, nullable=True)
    start_offset_ms = Column(Integer, nullable=True)
    end_offset_ms = Column(Integer, nullable=True)
    hit_ts = Column(DateTime(timezone=True), nullable=True)

    record = relationship("QcRecord", back_populates="violation_rows")


class QcAlert(Base, PublicFieldsMixin):
    __tablename__ = "qc_alerts"
    __table_args__ = (
        CheckConstraint("level IN ('high')", name="qc_alerts_level_check"),
        CheckConstraint("status IN ('NEW','ACKED')", name="qc_alerts_status_check"),
        CheckConstraint("is_delete IN ('0','1')", name="qc_alerts_is_delete_check"),
        Index("idx_qc_alerts_qc_record_id", "qc_record_id"),
        {"comment": "高危告警，level 仅 high"},
    )

    qc_record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("qc_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    violation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("qc_violations.id", ondelete="SET NULL"),
        nullable=True,
    )
    level = Column(String(10), nullable=False)
    status = Column(String(10), nullable=False, default="NEW", server_default="NEW")
    acked_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acked_at = Column(DateTime(timezone=True), nullable=True)

    record = relationship("QcRecord", back_populates="alerts")


class QcReview(Base, PublicFieldsMixin):
    __tablename__ = "qc_reviews"
    __table_args__ = (
        CheckConstraint("action IN ('CONFIRM','DISMISS','CORRECT')", name="qc_reviews_action_check"),
        CheckConstraint(
            "revised_score IS NULL OR (revised_score BETWEEN 0 AND 100)",
            name="qc_reviews_revised_score_check",
        ),
        CheckConstraint("is_delete IN ('0','1')", name="qc_reviews_is_delete_check"),
        Index("idx_qc_reviews_qc_record_id", "qc_record_id"),
        {"comment": "人工复核动作"},
    )

    qc_record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("qc_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(20), nullable=False)
    revised_score = Column(Integer, nullable=True)
    reason = Column(Text, nullable=True)

    record = relationship("QcRecord", back_populates="reviews")


class QcRule(Base, PublicFieldsMixin):
    __tablename__ = "qc_rules"
    __table_args__ = (
        CheckConstraint("status IN ('draft','active','archived')", name="qc_rules_status_check"),
        CheckConstraint("is_delete IN ('0','1')", name="qc_rules_is_delete_check"),
        Index("idx_qc_rules_active", "status", unique=True, postgresql_where=text("status = 'active'")),
        {"comment": "版本化质检规则配置表（R01~R15 规则包）"},
    )

    version = Column(String(50), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="draft", server_default="draft")
    rules = Column(JSONB, nullable=False)
    penalty = Column(JSONB, nullable=True)
    same_word_window_ms = Column(Integer, nullable=True, default=30000, server_default="30000")
    grade_thresholds = Column(JSONB, nullable=True)
    effective_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    effective_at = Column(DateTime(timezone=True), nullable=True)
    description = Column(String(255), nullable=True)
