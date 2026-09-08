"""LangGraph 整图编译与全链路 Mock 测试（含 interrupt resume）。"""
from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.graph.builder import build_complaint_graph
from app.models.state import ComplaintSessionState, TaskStatus


def _initial_state() -> dict:
    return ComplaintSessionState(
        session_id="s1",
        trace_id="t1",
        complaint_id="cmp1",
        complaint_text="用户投诉催收",
        case_ids=["c1"],
    ).model_dump()


class TestGraphCompile:
    def test_build_compiles(self):
        g = build_complaint_graph()
        assert g is not None


class TestFullChain:
    def test_evidence_to_resolved_via_approve(self):
        """全链路 Mock：evidence_gathering → human_review interrupt → resume approve → resolved。"""
        graph = build_complaint_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "chain-1"}}

        async def _run():
            state = await graph.ainvoke(_initial_state(), config)
            assert "__interrupt__" in state, "应在 human_review 处中断"
            final = await graph.ainvoke(Command(resume="approve"), config)
            return final

        final = asyncio.run(_run())
        assert final["task_status"] == TaskStatus.resolved.value
