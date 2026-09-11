"""strategy_versions 表 ORM 模型。"""
import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base import Base, PublicFieldsMixin


class StrategyVersion(Base, PublicFieldsMixin):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_type", "version", name="strategy_versions_strategy_type_version_key"),
        {"comment": "策略版本表，统一管理各类策略版本与审批，支持回滚"},
    )

    strategy_type: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
        comment="assignment/qc_scoring/call_flow/complaint_handling/review_classify",
    )
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft",
        comment="draft/active/archived",
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
