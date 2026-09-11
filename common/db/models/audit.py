"""audit_logs 表 ORM 模型（append-only）。"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

from common.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"comment": "操作审计日志表，append-only，哈希链防篡改"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id = Column(String(100), index=True, nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(50), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(String(100), nullable=True)
    detail = Column(JSONB, nullable=True)
    ip_address = Column(INET, nullable=True)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
