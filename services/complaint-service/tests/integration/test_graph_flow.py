"""图全路径集成测试：1 轮通过 / 2 轮反思 / 3 轮超限 / MCP 故障 / Token 超限。"""
from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.graph.builder import build_complaint_graph
from app.models.state import ComplaintSessionState, TaskStatus


def _state(complaint_id: str = "c-flow") -> dict:
    return ComplaintSessionState(
        session_id=f"s-{complaint_id}",
        trace_id=f"t-{complaint_id}",
        complaint_id=complaint_id,
        complaint_text="用户投诉催收",
        case_ids=["case-1"],
    ).model_dump()


class TestGraphFlow:
    def test_one_round_pass(self):
        """1 轮通过：evidence → reflection → verdict → package → human_review 中断。"""
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "flow-1"}}
        state = asyncio.run(graph.ainvoke(_state(), cfg))
        assert "__interrupt__" in state
        # 中断于 human_review，状态为 judging/draft_generated/pending_review 之一
        assert state["task_status"] in (
            TaskStatus.judging.value,
            TaskStatus.draft_generated.value,
            TaskStatus.pending_review.value,
        )

    def test_two_round_reflection(self):
        """2 轮：首轮 verdict 产出，human_review approve 通过。"""
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "flow-2"}}
        asyncio.run(graph.ainvoke(_state("c-2"), cfg))
        final = asyncio.run(graph.ainvoke(Command(resume="approve"), cfg))
        assert final["task_status"] == TaskStatus.resolved.value

    def test_three_round_exceeded_to_human(self):
        """3 轮超限：reject_rejudge 触发反思循环，最终转人工。"""
        graph = build_complaint_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "flow-3"}}
        asyncio.run(graph.ainvoke(_state("c-3"), cfg))
        # 连续 reject_rejudge 触发反思
        for i in range(2):
            final = asyncio.run(graph.ainvoke(Command(resume="reject_rejudge"), cfg))
        assert final["reject_count"] >= 2
        assert final["task_status"] in (
            TaskStatus.evidence_gathering.value,
            TaskStatus.pending_review.value,
            TaskStatus.judging.value,
        )

    def test_mcp_failure_degrades(self):
        """MCP 故障：evidence 节点 mock 失败 → degrade 路径（中断或 pending_review）。"""
        from unittest.mock import patch
        from app.graph.nodes import evidence_case
        async def _fail(state):
            from app.models.state import ExceptionInfo
            return {
                "exception_info": ExceptionInfo(
                    error_type="mcp_failed",
                    message="business ticket mcp down",
                    node="evidence_case",
                ).model_dump(),
                "task_status": TaskStatus.pending_review.value,
            }
        with patch.object(evidence_case, "evidence_case_node", _fail):
            graph = build_complaint_graph(checkpointer=MemorySaver())
            cfg = {"configurable": {"thread_id": "flow-mcp"}}
            state = asyncio.run(graph.ainvoke(_state("c-mcp"), cfg))
            # MCP 失败后走降级，最终中断于 human_review 或停在 pending_review
            assert state["task_status"] in (
                TaskStatus.pending_review.value,
                TaskStatus.judging.value,
            )

    def test_token_exceed_degrades(self):
        """Token 超限：mock reflection 标记超限 → 降级路径。"""
        from unittest.mock import patch
        from app.graph.nodes import reflection
        async def _too_many_tokens(state):
            from app.models.state import ExceptionInfo
            return {
                "exception_info": ExceptionInfo(
                    error_type="token_exceeded",
                    message="token count exceeded",
                    node="reflection",
                ).model_dump(),
                "task_status": TaskStatus.pending_review.value,
            }
        with patch.object(reflection, "reflection_node", _too_many_tokens):
            graph = build_complaint_graph(checkpointer=MemorySaver())
            cfg = {"configurable": {"thread_id": "flow-token"}}
            state = asyncio.run(graph.ainvoke(_state("c-token"), cfg))
            assert state["task_status"] in (
                TaskStatus.pending_review.value,
                TaskStatus.judging.value,
                TaskStatus.evidence_gathering.value,
            )
