"""SQLAlchemy 声明式基类 + 公共字段 Mixin。"""
import uuid
from datetime import datetime

from sqlalchemy import CHAR, Column, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class PublicFieldsMixin:
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    update_time = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    is_delete = Column(CHAR(1), nullable=False, default="0")
