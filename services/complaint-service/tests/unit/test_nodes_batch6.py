"""批次6 节点冒烟单测（4.9 / 4.10 / 4.11 / 4.13）。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.mcp.errors import MCPError
from app.models.state import (
    ComplaintSessionState,
    ComplianceOpinion,
    FactTimeline,
    ReviewAction,
    TaskStatus,
    UserDemandAnalysis,
    VerdictDraft,
    VerdictType,
)
from app.graph.nodes import human_review as hr_mod
from app.graph.nodes.verdict import verdict_node
from app.graph.nodes.generate_package import generate_package_node
from app.graph.nodes.sample_backflow import sample_backflow_node
from app.graph.nodes.judgment_fanout import judgment_fanout_node


def _state(**kw) -> ComplaintSessionState:
    base = dict(session_id="s1", trace_id="t1", complaint_id="cmp1", complaint_text="x")
    base.update(kw)
    return ComplaintSessionState(**base)


def _full_state() -> ComplaintSessionState:
    return _state(
        fact_timeline=FactTimeline(summary="s"),
        compliance_opinion=ComplianceOpinion(overall_compliance="compliant"),
        user_demand=UserDemandAnalysis(
            demand_type="refund", emotion_level="calm", secondary_complaint_risk="low"
        ),
    )


class TestVerdict:
    def test_none_defense_to_pending_review(self):
        out = asyncio.run(verdict_node(_state()))
        assert out["exception_info"]["error_type"] == "llm_degraded"
        assert out["task_status"] == "pending_review"

    def test_produces_verdict_draft(self):
        out = asyncio.run(verdict_node(_full_state()))
        assert out["verdict_draft"] is not None
        assert out["verdict_draft"].verdict in set(VerdictType)


class TestGeneratePackage:
    def _with_verdict(self) -> ComplaintSessionState:
        return _state(
            verdict_draft=VerdictDraft(verdict=VerdictType.pending, confidence=0.8),
        )

    def test_produces_judgment_package(self):
        out = asyncio.run(generate_package_node(self._with_verdict()))
        assert out["judgment_package"] is not None

    def test_mcp_degradation(self):
        async def _boom(*a, **k):
            raise MCPError("rag_kb", "search_response_templates", "boom")

        with patch("app.graph.nodes.generate_package.get_mcp_client") as gm:
            gm.return_value.call = AsyncMock(side_effect=_boom)
            out = asyncio.run(generate_package_node(self._with_verdict()))
        assert out["judgment_package"] is not None
        assert out["exception_info"]["error_type"] == "MCPError"

    def test_short_circuit_without_verdict(self):
        with patch("app.graph.nodes.generate_package.get_mcp_client") as gm:
            with patch("app.graph.nodes.generate_package.get_llm_provider") as gl:
                out = asyncio.run(generate_package_node(_state()))
        assert out == {}
        gm.assert_not_called()
        gl.assert_not_called()

    def test_short_circuit_pending_review(self):
        s = self._with_verdict()
        s.task_status = TaskStatus.pending_review
        with patch("app.graph.nodes.generate_package.get_mcp_client") as gm:
            out = asyncio.run(generate_package_node(s))
        assert out == {}
        gm.assert_not_called()


class TestHumanReview:
    def _run(self, action):
        with patch.object(hr_mod, "interrupt", return_value=action):
            return asyncio.run(hr_mod.human_review_node(_state()))

    def test_approve_resolved(self):
        out = self._run("approve")
        assert out["task_status"] == "resolved"

    def test_reject_rejudge_increments_count(self):
        out = self._run("reject_rejudge")
        assert out["task_status"] == "evidence_gathering"
        assert out["human_review"]["reject_count"] == 1

    def test_escalate_senior_review(self):
        out = self._run("escalate")
        assert out["task_status"] == "pending_senior_review"


class TestSampleBackflow:
    def test_success(self):
        out = asyncio.run(sample_backflow_node(_state()))
        assert out == {}

    def test_retry_does_not_block(self):
        async def _boom(*a, **k):
            raise MCPError("data_platform", "write_session", "boom")

        with patch("app.graph.nodes.sample_backflow.get_mcp_client") as gm:
            gm.return_value.call = AsyncMock(side_effect=_boom)
            out = asyncio.run(sample_backflow_node(_state()))
        assert out == {}


class TestJudgmentFanout:
    def test_only_sets_judging(self):
        out = asyncio.run(judgment_fanout_node(_state()))
        assert out == {"task_status": "judging"}
