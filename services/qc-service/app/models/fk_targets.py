"""注册他服务表到同一 metadata，供 qc ORM 解析 FK（本服务不拥有这些表）。"""
from sqlalchemy import Column, String, Table
from sqlalchemy.dialects.postgresql import UUID

from common.db.base import Base
from common.db.models.user import User  # noqa: F401  — users.id

_REGISTERED = False


def register_fk_targets() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    if "cases" not in Base.metadata.tables:
        Table(
            "cases",
            Base.metadata,
            Column("id", UUID(as_uuid=True), primary_key=True),
        )
    if "call_records" not in Base.metadata.tables:
        Table(
            "call_records",
            Base.metadata,
            Column("id", UUID(as_uuid=True), primary_key=True),
            Column("session_id", String(100), unique=True, nullable=False),
        )
    _REGISTERED = True


register_fk_targets()
