"""human_review 集成测试：judgment_fanout、evidence_re_gather、驳回重判。"""
from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.graph.builder import build_complaint_graph
from app.models.state import ComplaintSessionState, TaskStatus


def _state(cid: str = "c-hr") -> dict:
    return ComplaintSessionState(
        session_id=f"s-{cid}", trace_id=f"t-{cid}",
        complaint_id=cid, complaint_text="投诉", case_ids=["c1"],
    ).model_dump()


class TestHumanReview:
    def test_fanout_nodes_exist(self):
        """judgment_fanout 与 evidence_re_gather 节点在图中注册。"""
        graph = build_complaint_graph()
        nodes = graph.get_graph().nodes
        assert "judgment_fanout" in nodes
        assert "evidence_re_gather" in nodes

    def test_reject_rejudge_goes_to_evidence_re_gather(self):
        """驳回重判 → human_review 返回 evidence_gathering，图走 re_gather 路径。"""
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "hr-reject"}}
        asyncio.run(graph.ainvoke(_state("c-r1"), cfg))
        final = asyncio.run(graph.ainvoke(Command(resume="reject_rejudge"), cfg))
        assert final["reject_count"] == 1
        # resume 后 human_review 节点设置 evidence_gathering，图继续到 re_gather
        assert final["task_status"] in (
            TaskStatus.evidence_gathering.value,
            TaskStatus.judging.value,
        )

    def test_add_evidence_re_gathers(self):
        """add_evidence → evidence_gathering 重新采集。"""
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "hr-add"}}
        asyncio.run(graph.ainvoke(_state("c-a1"), cfg))
        final = asyncio.run(graph.ainvoke(Command(resume="add_evidence"), cfg))
        assert final["task_status"] == TaskStatus.evidence_gathering.value

    def test_escalate_to_senior(self):
        """escalate → pending_senior_review。"""
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "hr-esc"}}
        asyncio.run(graph.ainvoke(_state("c-e1"), cfg))
        final = asyncio.run(graph.ainvoke(Command(resume="escalate"), cfg))
        assert final["task_status"] == TaskStatus.pending_senior_review.value
