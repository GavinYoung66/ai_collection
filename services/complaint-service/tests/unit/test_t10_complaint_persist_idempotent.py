"""T10：工单落库原子幂等（ON CONFLICT DO NOTHING，不再 SELECT-then-INSERT）。"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.service.complaint_service import ComplaintPersistError, complaint_service


class _Result:
    def __init__(self, *, rowcount: int | None = None, row: Any = None) -> None:
        self.rowcount = rowcount
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _FakeSession:
    """最小 AsyncSession 替身：按顺序返回预置结果并记录事务动作。"""

    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.statements: list[Any] = []
        self.committed = 0
        self.rolled_back = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        if not self._results:
            raise AssertionError("unexpected extra execute()")
        return self._results.pop(0)

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


def _rec(case_id: str) -> dict[str, str]:
    return {"complaint_id": case_id, "case_id": case_id}


@pytest.mark.asyncio
async def test_insert_uses_on_conflict_do_nothing(monkeypatch):
    """正常流程：单条 INSERT ... ON CONFLICT DO NOTHING 原子落库。"""
    case_id = str(uuid.uuid4())
    session = _FakeSession([_Result(rowcount=1)])
    monkeypatch.setattr("common.db.session.async_session_factory", lambda: session)

    await complaint_service._persist_complaint_row(_rec(case_id), "投诉内容")

    assert session.committed == 1
    assert len(session.statements) == 1
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "INSERT INTO complaints" in sql
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql


@pytest.mark.asyncio
async def test_conflict_with_active_row_is_idempotent_success(monkeypatch):
    """并发后到请求：冲突 rowcount=0 → 按已存在处理，不报错、不重复插。"""
    case_id = str(uuid.uuid4())
    existing = type("Row", (), {"is_delete": "0"})()
    session = _FakeSession([_Result(rowcount=0), _Result(row=existing)])
    monkeypatch.setattr("common.db.session.async_session_factory", lambda: session)

    await complaint_service._persist_complaint_row(_rec(case_id), "并发重复")

    assert session.committed == 0
    assert len(session.statements) == 2
    assert session.statements[1].is_select


@pytest.mark.asyncio
async def test_conflict_with_soft_deleted_row_is_restored(monkeypatch):
    """边界：同 id 软删行冲突 → 恢复为活跃行，仍只保留一条记录。"""
    case_id = str(uuid.uuid4())
    existing = type(
        "Row", (), {"is_delete": "1", "complaint_text": "old", "status": "closed"}
    )()
    session = _FakeSession([_Result(rowcount=0), _Result(row=existing)])
    monkeypatch.setattr("common.db.session.async_session_factory", lambda: session)

    await complaint_service._persist_complaint_row(_rec(case_id), "恢复内容")

    assert existing.is_delete == "0"
    assert existing.complaint_text == "恢复内容"
    assert existing.status == "pending"
    assert session.committed == 1


@pytest.mark.asyncio
async def test_invalid_uuid_raises_without_touching_db(monkeypatch):
    """异常：非法 case_id 在连接数据库前即拒绝。"""
    factory_calls: list[int] = []

    def factory():
        factory_calls.append(1)
        raise AssertionError("DB must not be opened for invalid uuid")

    monkeypatch.setattr("common.db.session.async_session_factory", factory)

    with pytest.raises(ComplaintPersistError, match="case_id 非法 UUID"):
        await complaint_service._persist_complaint_row(
            {"complaint_id": "not-a-uuid", "case_id": "not-a-uuid"}, "x"
        )
    assert factory_calls == []


@pytest.mark.asyncio
async def test_db_error_rolls_back_and_wraps(monkeypatch):
    """异常：数据库执行失败 → 回滚并以 ComplaintPersistError 显式上抛。"""

    class _BoomSession(_FakeSession):
        async def execute(self, stmt: Any) -> _Result:
            self.statements.append(stmt)
            raise RuntimeError("db down")

    session = _BoomSession([])
    monkeypatch.setattr("common.db.session.async_session_factory", lambda: session)

    with pytest.raises(ComplaintPersistError, match="投诉工单落库失败"):
        await complaint_service._persist_complaint_row(_rec(str(uuid.uuid4())), "x")
    assert session.rolled_back == 1
