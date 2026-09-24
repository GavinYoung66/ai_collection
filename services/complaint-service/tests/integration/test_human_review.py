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


def _batch2_state(cid: str) -> dict:
    """批次②：与 run_min_loop 一致的入参形状（投诉文案 + case_id 命中 mock fixture）。"""
    return ComplaintSessionState(
        session_id=f"s-{cid}",
        trace_id=f"t-{cid}",
        complaint_id=cid,
        complaint_text="用户投诉催收",
        case_ids=["case-1"],
    ).model_dump()


class TestBatch2GraphPersistence:
    """批次② 3.1/3.2/3.3：进程级 MemorySaver + thread_id=complaint_id + review 恢复。"""

    def test_thread_id_complaint_id_state_kept_and_resumed_to_backflow(self):
        """judge 与 review 各自编译图但共享同一进程级 checkpointer：

        - 3.1 judge 停 human_review 后，用 thread_id=complaint_id 可取回挂起状态；
        - 3.2 Command(resume="approve") 恢复后人审 → sample_backflow 推进到 END；
        - 3.3 sample_backflow 经 mcp.call 调 data_platform.write_session。
        """
        from app.api.deps import get_mcp_client
        from app.service.complaint_service import _get_graph_checkpointer

        cid = "c-b2-persist-001"
        saver = _get_graph_checkpointer()
        cfg = {"configurable": {"thread_id": cid}}

        # judge 端：编译图 A，跑到 human_review 中断
        graph_judge = build_complaint_graph(checkpointer=saver)
        out = asyncio.run(graph_judge.ainvoke(_batch2_state(cid), cfg))
        assert "__interrupt__" in out

        # 3.1：review 端另编译图 B，同一 saver + 同一 thread_id=complaint_id 取回挂起点
        graph_review = build_complaint_graph(checkpointer=saver)
        snap = asyncio.run(graph_review.aget_state(cfg))
        assert tuple(snap.next) == ("human_review",)

        # 3.2：approve 恢复 → sample_backflow → END（resolved）
        final = asyncio.run(
            graph_review.ainvoke(Command(resume="approve"), cfg)
        )
        assert final["task_status"] == TaskStatus.resolved.value
        snap_after = asyncio.run(graph_review.aget_state(cfg))
        assert tuple(snap_after.next) == ()

        # 3.3：sample_backflow 节点经 mcp.call 调了 write_session
        calls = [(c["server"], c["tool"]) for c in get_mcp_client().call_log]
        assert ("data_platform", "write_session") in calls
        assert ("data_platform", "write_human_review_truth") in calls

    def test_service_resume_graph_thread_reuses_same_thread(self):
        """3.2/3.3 生产方法：complaint_service._resume_graph_thread 用同一进程级

        checkpointer 与 thread_id=complaint_id 恢复图，不新建线程。
        """
        from app.api.deps import get_mcp_client
        from app.service.complaint_service import (
            _get_graph_checkpointer,
            complaint_service,
        )

        cid = "c-b2-svc-resume-001"
        cfg = {"configurable": {"thread_id": cid}}
        graph = build_complaint_graph(checkpointer=_get_graph_checkpointer())
        out = asyncio.run(graph.ainvoke(_batch2_state(cid), cfg))
        assert "__interrupt__" in out

        # 生产恢复入口（review approve 后调度的协程），session_id 仅用于日志
        asyncio.run(complaint_service._resume_graph_thread(cid, f"s-{cid}"))

        snap = asyncio.run(graph.aget_state(cfg))
        assert tuple(snap.next) == ()
        assert snap.values["task_status"] == TaskStatus.resolved.value
        calls = [(c["server"], c["tool"]) for c in get_mcp_client().call_log]
        assert ("data_platform", "write_session") in calls


class TestBatch2ResumeGuards:
    """G1b/G2b：双驱动与 HTTP 5s 超时重试下的图执行互斥与精确 resume 判定。"""

    def test_resume_skips_when_graph_not_at_interrupt(self, monkeypatch):
        """G2b：图未停在 human_review 断点时（judge 未跑完 / 被双驱动打断），

        resume 必须跳过而非空转 Command(resume)，否则 checkpoint 被污染，
        sample_backflow / write_session 永不执行。
        """
        from app.api.deps import get_mcp_client
        from app.service import complaint_service as cs_module
        from app.service.complaint_service import (
            _get_graph_checkpointer,
            complaint_service,
        )

        monkeypatch.setattr(cs_module, "_RESUME_WAIT_TIMEOUT_S", 0.2)
        monkeypatch.setattr(cs_module, "_RESUME_WAIT_INTERVAL_S", 0.02)

        cid = "c-b2-resume-guard-001"
        cfg = {"configurable": {"thread_id": cid}}
        graph = build_complaint_graph(checkpointer=_get_graph_checkpointer())
        before = len(get_mcp_client().call_log)

        # 全新线程：图从未运行 → 非可恢复态 → 不应发 resume
        asyncio.run(complaint_service._resume_graph_thread(cid, f"s-{cid}"))

        snap = asyncio.run(graph.aget_state(cfg))
        assert tuple(snap.next) == ()
        assert len(get_mcp_client().call_log) == before

    def test_judge_async_skips_while_graph_inflight(self):
        """G1b：同工单真图在途时 judge_async(run_graph=True) 直接返回当前态，

        不并发跑第二遍图（避免同 thread_id checkpoint 分叉）。
        """
        from app.service import complaint_service as cs_module
        from app.service.complaint_service import complaint_service

        cid = "c-b2-inflight-001"
        rec = complaint_service.create("投诉", case_id=cid)
        cs_module._graph_inflight.add(cid)
        try:
            out = asyncio.run(complaint_service.judge_async(cid, run_graph=True))
        finally:
            cs_module._graph_inflight.discard(cid)

        assert out["complaint_id"] == rec["complaint_id"]
        assert out["task_status"] == TaskStatus.pending.value
