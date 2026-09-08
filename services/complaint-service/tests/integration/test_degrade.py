"""11 降级场景集成测试：LLM 3 + MCP 4 + 流程 4。"""
from __future__ import annotations

import pytest

from app.events.publisher import publisher
from app.service.degrade_flow import DegradeFlow
from app.service.degrade_llm import DegradeLLM
from app.service.degrade_mcp import DegradeMCP


@pytest.fixture(autouse=True)
def _clear():
    publisher.clear()
    yield


def _codes(state: dict) -> set[str]:
    return {e["code"] for e in state.get("exception_info", [])}


class TestDegradeScenarios:
    def test_llm_three_scenarios(self):
        d = DegradeLLM()
        st: dict = {}
        d.handle_judge_failure(st)
        d.handle_output_failure(st)
        d.handle_rag_failure(st)
        assert _codes(st) == {"A5004", "A5010", "A5005"}

    def test_mcp_four_scenarios(self):
        m = DegradeMCP()
        st: dict = {}
        m.handle_business_ticket_failure(st)
        m.handle_call_quality_failure(st)
        codes = _codes(st)
        assert "A5006" in codes and "A5008" in codes
        # 回写失败不阻塞，且写入 A5013
        assert m.handle_writeback_failure(state=st) is False
        assert "A5013" in _codes(st)
        # 权限拒绝终止
        assert m.handle_permission_denied("mcp", "rag") is False

    def test_flow_four_scenarios(self):
        f = DegradeFlow()
        st: dict = {}
        assert f.handle_loop_exceeded(3, st) == "pending_review"
        assert f.handle_steps_exceeded(100, 50, st) == "pending_review"
        assert f.handle_token_exceeded(9999, 1000, st) == "pending_review"
        assert f.handle_global_timeout(40 * 60, st) == "pending_review"
        assert st["task_status"] == "pending_review"

    def test_degraded_event_published(self):
        DegradeFlow().handle_loop_exceeded(3, {})
        topics = [e.topic for e in publisher.list_events()]
        assert "complaint.degraded" in topics

    def test_all_eleven_codes(self):
        """11 场景错误码集合对齐 design（含回写 A5013；权限拒绝无业务码）。"""
        llm = DegradeLLM()
        mcp = DegradeMCP()
        flow = DegradeFlow()
        st: dict = {}
        llm.handle_judge_failure(st)
        llm.handle_output_failure(st)
        llm.handle_rag_failure(st)
        mcp.handle_business_ticket_failure(st)
        mcp.handle_call_quality_failure(st)
        mcp.handle_writeback_failure(state=st)
        assert mcp.handle_permission_denied("mcp", "rag") is False
        flow.handle_loop_exceeded(3, st)
        flow.handle_steps_exceeded(100, 50, st)
        flow.handle_token_exceeded(9999, 1000, st)
        flow.handle_global_timeout(40 * 60, st)
        codes = _codes(st)
        expected = {"A5002", "A5004", "A5005", "A5006", "A5008", "A5010", "A5013"}
        assert expected.issubset(codes)
