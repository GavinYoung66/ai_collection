"""users 表 ORM 模型。"""
import uuid

from sqlalchemy import CHAR, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base import Base, PublicFieldsMixin


class User(Base, PublicFieldsMixin):
    __tablename__ = "users"
    __table_args__ = {"comment": "系统用户与坐席主表，承载 RBAC 角色与坐席技能画像"}

    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="bcrypt 哈希，cost factor=12，禁止明文存储")
    real_name: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        comment="admin/supervisor/agent/qc_reviewer/ai_trainer",
    )
    agent_skills: Mapped[dict] = mapped_column(JSONB, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active", index=True,
        comment="active/disabled/locked",
    )
    last_login_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
