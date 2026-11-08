"""T96：llm_reviewer 埋点兜底 logger 定义（spec observability：旁路埋点故障不阻断主流程）。

`_write_log` 独立短事务持久化失败时此前引用未定义 `logger` → NameError 传播为
A2001 PARTIAL，违反"埋点不阻塞质检"声明；补 logger 后 DB 闪断仅 warning，
质检结论不变。
"""
from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from app.services.llm_reviewer import LLMReviewer
from app.services.llm_reviewer import logger as reviewer_logger


async def _empty_llm(_messages):
    return "[]"


def test_module_logger_defined():
    """模块级 logger 已定义（T96 NameError 根因消除）。"""
    assert reviewer_logger is not None


@pytest.mark.asyncio
async def test_write_log_db_flash_warning_no_nameerror(monkeypatch):
    """DB 闪断模拟：独立短事务持久化失败走 warning，不抛 NameError。"""
    from common.db import session as db_session_mod

    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("db flash down")

    monkeypatch.setattr(db_session_mod, "async_session_factory", _BoomFactory())
    reviewer = LLMReviewer(invoke=_empty_llm, persist_call_logs=True)

    with capture_logs() as logs:
        await reviewer._write_log(
            prompt="p", response="r", status="success", session_id="s1"
        )

    warnings = [e for e in logs if e.get("event") == "llm_call_log_persist_failed"]
    assert warnings, "DB 闪断应留 warning 日志（旁路埋点故障不阻断主流程）"


@pytest.mark.asyncio
async def test_write_log_persist_success_no_warning(monkeypatch):
    """持久化正常路径不产生 warning（守护不误报）。"""
    from common.db import session as db_session_mod

    class _FakeSession:
        def add(self, obj):
            return None

        async def commit(self):
            return None

    class _FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_session_mod, "async_session_factory", _FakeFactory())
    reviewer = LLMReviewer(invoke=_empty_llm, persist_call_logs=True)

    with capture_logs() as logs:
        await reviewer._write_log(
            prompt="p", response="r", status="success", session_id="s1"
        )

    assert not [e for e in logs if e.get("event") == "llm_call_log_persist_failed"]