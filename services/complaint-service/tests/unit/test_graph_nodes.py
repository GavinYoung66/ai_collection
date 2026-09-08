"""LangGraph 各节点 Mock 冒烟测试（含 4.7 MCP 降级）。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.mcp.errors import MCPTimeoutError
from app.models.state import ComplaintSessionState
from app.graph.nodes.orchestrator import orchestrator_node
from app.graph.nodes.evidence_case import evidence_case_node
from app.graph.nodes.evidence_call_qc import evidence_call_qc_node
from app.graph.nodes.evidence_re_gather import evidence_re_gather_node
from app.graph.nodes.reflection import reflection_node
from app.graph.nodes.fact import fact_node
from app.graph.nodes.compliance import compliance_node
from app.graph.nodes.user_demand import user_demand_node


def _state(**kw) -> ComplaintSessionState:
    base = dict(session_id="s1", trace_id="t1", complaint_id="cmp1", complaint_text="x")
    base.update(kw)
    return ComplaintSessionState(**base)


def test_orchestrator_init_and_review():
    assert asyncio.run(orchestrator_node(_state()))["task_status"] == "evidence_gathering"
    out = asyncio.run(orchestrator_node(_state(exception_info={"err": "e"})))
    assert out["task_status"] == "pending_review"


def test_evidence_case():
    out = asyncio.run(evidence_case_node(_state(case_ids=["c1"])))
    assert "c1" in out["case_evidence"]


def test_evidence_call_qc():
    out = asyncio.run(evidence_call_qc_node(_state(case_ids=["c1"])))
    assert "c1" in out["case_evidence"]


def test_evidence_re_gather_clears_exception():
    out = asyncio.run(evidence_re_gather_node(_state(exception_info={"err": "e"})))
    assert out["task_status"] == "evidence_gathering"
    assert out["exception_info"] is None


def test_reflection_increments_loop():
    out = asyncio.run(reflection_node(_state(loop_count=0)))
    assert out["reflection_report"] is not None
    assert out["loop_count"] == 1


def test_fact_produces_timeline():
    out = asyncio.run(fact_node(_state()))
    assert out["fact_timeline"] is not None


def test_compliance_happy_path():
    out = asyncio.run(compliance_node(_state()))
    assert out["compliance_opinion"] is not None
    assert out.get("exception_info") is None


def test_compliance_mcp_degradation():
    """4.7 MCP 降级：search_regulations 抛 MCPTimeoutError → 仍产出合规意见 + exception_info。"""

    async def _boom(*a, **k):
        raise MCPTimeoutError("rag_kb", "search_regulations", "timeout")

    with patch("app.graph.nodes.compliance.get_mcp_client") as gm:
        gm.return_value.call = AsyncMock(side_effect=_boom)
        out = asyncio.run(compliance_node(_state()))
    assert out["compliance_opinion"] is not None
    assert out["exception_info"]["error_type"] == "MCPTimeoutError"


def test_user_demand():
    out = asyncio.run(user_demand_node(_state()))
    assert out["user_demand"] is not None
