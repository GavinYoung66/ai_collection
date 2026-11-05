"""T54 / 4.1：aensure_persisted 幂等写入对齐 T10 ON CONFLICT 形态 单测。

验收判据：
- asyncio.gather 双请求同 session_id 均返回 True 且 ``_persist_degraded``
  未置位（无误报持久化降级）；
- 语句形态为 pg_insert + ON CONFLICT DO NOTHING（index_elements=session_id）；
- IntegrityError 并发窗口兜底：仅 unique_violation（pgcode 23505，W1 收窄）
  视为幂等 True、不置降级；
- 其余异常（含 FK/CHECK 等非 23505 的 IntegrityError）仍走 fail-open 降级
  （False + 置位，语义保留）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.service.session_service import SessionService


class _FakePgOrig(Exception):
    """模拟 asyncpg 原始异常：携带 pgcode 供 W1 收窄判定。"""

    def __init__(self, pgcode: str):
        super().__init__(f"pg error {pgcode}")
        self.pgcode = pgcode


class _FakeResult:
    rowcount = 1


class _FakeDB:
    """async_session_factory 返回的最小 async context manager。"""

    def __init__(self, execute_error: Exception | None = None):
        self._execute_error = execute_error
        self.captured_stmt = None
        self.execute = AsyncMock(side_effect=self._execute)
        self.commit = AsyncMock()

    async def _execute(self, stmt):
        self.captured_stmt = stmt
        if self._execute_error is not None:
            raise self._execute_error
        return _FakeResult()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _make_svc_with_record(monkeypatch) -> tuple[SessionService, str]:
    """创建带记录的 service；complaint_id 必须为合法 UUID（persist 写入 FK 校验）。"""
    monkeypatch.setattr(settings, "COMPLAINT_SESSION_PERSIST", True)
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    svc = SessionService()
    cid = str(uuid.uuid4())
    svc.create_session(cid, "投诉测试内容")
    return svc, cid


class TestAensurePersistedIdempotent:
    @pytest.mark.asyncio
    async def test_concurrent_dual_requests_both_true_no_degraded(
        self, monkeypatch
    ):
        """并发双请求同 session_id：均返回 True 且不误报持久化降级。"""
        svc, cid = _make_svc_with_record(monkeypatch)
        sid = svc._find_by_complaint(cid)
        monkeypatch.setattr(
            "common.db.session.async_session_factory", lambda: _FakeDB()
        )

        results = await asyncio.gather(
            svc.aensure_persisted(sid), svc.aensure_persisted(sid)
        )

        assert results == [True, True]
        assert svc.persist_degraded is False, "并发窗口不得误报持久化降级"

    @pytest.mark.asyncio
    async def test_statement_uses_on_conflict_do_nothing(self, monkeypatch):
        """语句形态：pg_insert + ON CONFLICT DO NOTHING（index_elements=session_id）。"""
        svc, cid = _make_svc_with_record(monkeypatch)
        sid = svc._find_by_complaint(cid)
        db = _FakeDB()
        monkeypatch.setattr(
            "common.db.session.async_session_factory", lambda: db
        )

        assert await svc.aensure_persisted(sid) is True

        stmt = db.captured_stmt
        assert stmt is not None
        clause = getattr(stmt, "_post_values_clause", None)
        assert clause is not None
        assert clause.__class__.__name__ == "OnConflictDoNothing", (
            "MUST 为 ON CONFLICT DO NOTHING 原子幂等语句（T10 形态）"
        )
        # 用编译产物断言冲突目标（不依赖 SQLAlchemy 内部属性名）
        from sqlalchemy.dialects import postgresql

        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (session_id) DO NOTHING" in compiled, (
            f"编译 SQL 缺 ON CONFLICT (session_id)：{compiled}"
        )

    @pytest.mark.asyncio
    async def test_integrity_error_unique_violation_idempotent_true_no_degraded(
        self, monkeypatch
    ):
        """IntegrityError(unique_violation 23505) 并发窗口兜底：幂等 True、不置降级。"""
        svc, cid = _make_svc_with_record(monkeypatch)
        sid = svc._find_by_complaint(cid)
        monkeypatch.setattr(
            "common.db.session.async_session_factory",
            lambda: _FakeDB(execute_error=IntegrityError(
                "stmt", {}, _FakePgOrig("23505"))
            ),
        )

        assert await svc.aensure_persisted(sid) is True
        assert svc.persist_degraded is False

    @pytest.mark.asyncio
    async def test_integrity_error_fk_violation_degraded_not_idempotent(
        self, monkeypatch
    ):
        """W1 收窄：非 23505 的 IntegrityError（如 FK 23503）属真实写入失败，
        MUST NOT 吞成幂等 True——返回 False + 置降级。"""
        svc, cid = _make_svc_with_record(monkeypatch)
        sid = svc._find_by_complaint(cid)
        monkeypatch.setattr(
            "common.db.session.async_session_factory",
            lambda: _FakeDB(execute_error=IntegrityError(
                "stmt", {}, _FakePgOrig("23503"))
            ),
        )

        assert await svc.aensure_persisted(sid) is False
        assert svc.persist_degraded is True

    @pytest.mark.asyncio
    async def test_integrity_error_without_pgcode_degraded(self, monkeypatch):
        """无 pgcode 的 IntegrityError（无法判定为唯一冲突）保守走降级分支。"""
        svc, cid = _make_svc_with_record(monkeypatch)
        sid = svc._find_by_complaint(cid)
        monkeypatch.setattr(
            "common.db.session.async_session_factory",
            lambda: _FakeDB(execute_error=IntegrityError(
                "stmt", {}, Exception("dup key"))
            ),
        )

        assert await svc.aensure_persisted(sid) is False
        assert svc.persist_degraded is True

    @pytest.mark.asyncio
    async def test_other_exception_still_fail_open_degraded(self, monkeypatch):
        """其余异常仍走 fail-open：返回 False + 置降级（语义保留）。"""
        svc, cid = _make_svc_with_record(monkeypatch)
        sid = svc._find_by_complaint(cid)
        monkeypatch.setattr(
            "common.db.session.async_session_factory",
            lambda: _FakeDB(execute_error=RuntimeError("pg down")),
        )

        assert await svc.aensure_persisted(sid) is False
        assert svc.persist_degraded is True

    @pytest.mark.asyncio
    async def test_persist_gate_disabled_returns_false(self, monkeypatch):
        """_persist_enabled() 门保留：mock 模式直接 False，不触 PG。"""
        svc = SessionService()
        sid = svc.create_session("c-gate", "内容")
        monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
        assert await svc.aensure_persisted(sid) is False