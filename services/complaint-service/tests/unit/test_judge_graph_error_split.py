"""_judge_with_graph 三分支拆分单测（spec: complaint-service/session-service）。

覆盖：
1. graph 抛 GraphInterrupt（正常人审挂起）→ pending_review，不写 exception_info；
2. graph 抛 TimeoutError → pending_review + exception_info.type=timeout；
3. graph 抛其他 Exception → pending_review + exception_info.type=unknown_error。
用 stub graph（monkeypatch build_complaint_graph）直接驱动异常路径。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.errors import GraphInterrupt

from app.models.state import TaskStatus
from app.service.complaint_service import complaint_service
from app.service.session_service import session_service


class _StubGraph:
    """astream 即抛指定异常的 stub 图；aget_state 返回空终态快照。"""

    def __init__(self, exc: BaseException | None = None):
        self._exc = exc

    def astream(self, _state, _config, stream_mode="updates"):  # noqa: ARG002 — stub 签名对齐
        return self._astream()

    async def _astream(self):
        if self._exc is not None:
            raise self._exc
        yield {}

    async def aget_state(self, _config):
        return SimpleNamespace(values={})


def _install_stub(monkeypatch, exc: BaseException | None) -> _StubGraph:
    stub = _StubGraph(exc)
    monkeypatch.setattr("app.graph.builder.build_complaint_graph", lambda **_kwargs: stub)
    return stub


def _make_rec() -> dict:
    return complaint_service.create(complaint_text="要求退款，客服不处理")


def _assert_pending_review(sid: str) -> None:
    session = session_service.get_session(sid)
    assert session is not None
    status = session["task_status"]
    value = status.value if hasattr(status, "value") else str(status)
    assert value == TaskStatus.pending_review.value


@pytest.mark.asyncio
async def test_graph_interrupt_no_exception_info(monkeypatch):
    """GraphInterrupt → pending_review 且 exception_info 未写。"""
    _install_stub(monkeypatch, GraphInterrupt())
    rec = _make_rec()
    sid = rec["session_id"]

    result = await complaint_service._judge_with_graph(rec, rec["complaint_id"])

    _assert_pending_review(sid)
    assert "exception_info" not in result
    session = session_service.get_session(sid)
    assert session is not None and "exception_info" not in session


@pytest.mark.asyncio
async def test_graph_timeout_writes_exception_info(monkeypatch):
    """TimeoutError → pending_review + exception_info.type=timeout。"""
    _install_stub(monkeypatch, TimeoutError("mock llm timeout"))
    rec = _make_rec()
    sid = rec["session_id"]

    result = await complaint_service._judge_with_graph(rec, rec["complaint_id"])

    _assert_pending_review(sid)
    assert result["exception_info"]["error_type"] == "timeout"
    assert result["exception_info"]["node"] == "graph"
    session = session_service.get_session(sid)
    assert session is not None
    assert session["exception_info"]["error_type"] == "timeout"


@pytest.mark.asyncio
async def test_graph_unknown_error_writes_unknown_error(monkeypatch):
    """兜底 Exception → pending_review + exception_info.type=unknown_error。"""
    _install_stub(monkeypatch, ValueError("boom"))
    rec = _make_rec()
    sid = rec["session_id"]

    result = await complaint_service._judge_with_graph(rec, rec["complaint_id"])

    _assert_pending_review(sid)
    assert result["exception_info"]["error_type"] == "unknown_error"
    session = session_service.get_session(sid)
    assert session is not None
    assert session["exception_info"]["error_type"] == "unknown_error"


@pytest.mark.asyncio
async def test_graph_llm_rate_limited_writes_explicit_type(monkeypatch):
    """LLMRateLimitError（RuntimeError 子类）→ pending_review + error_type=llm_rate_limited。

    回归第三轮审计 P3-3：不得再把裸 "RuntimeError" 落 exception_info。
    """
    from common.llm.reliability import LLMRateLimitError

    _install_stub(monkeypatch, LLMRateLimitError("LLM rate limit exceeded: csmp:llm_ratelimit:m"))
    rec = _make_rec()
    sid = rec["session_id"]

    result = await complaint_service._judge_with_graph(rec, rec["complaint_id"])

    _assert_pending_review(sid)
    assert result["exception_info"]["error_type"] == "llm_rate_limited"
    session = session_service.get_session(sid)
    assert session is not None
    assert session["exception_info"]["error_type"] == "llm_rate_limited"
