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


# ── 阶段2（2.2/2.3）：单 case 内证据查询 gather 并发 + 异常契约 ──


def test_evidence_case_concurrent_gather():
    """2.2：单 case 内三查询并发——总耗时≈最慢单次，三查询时间窗存在共同重叠。"""
    import time

    from app.graph.nodes import evidence_case as mod

    stamps: dict[str, list[float]] = {}

    async def _delayed_call(server, tool, params, *, trace_id="", session_id=""):
        stamps.setdefault(tool, []).append(time.monotonic())
        await asyncio.sleep(0.1)
        stamps[tool].append(time.monotonic())
        if tool == "query_case_info":
            return {"case_id": params["case_id"]}
        return []

    mock = AsyncMock()
    mock.call = _delayed_call
    with patch.object(mod, "get_mcp_client", return_value=mock):
        start = time.monotonic()
        out = asyncio.run(mod.evidence_case_node(_state(case_ids=["c1"])))
        elapsed = time.monotonic() - start

    # 耗时断言为防串行退化兜底（阈值贴近串行和 0.3）；主证据是下方窗口断言
    # （确定性逻辑断言，串行实现下 max(starts)<min(ends) 必然不成立）
    assert elapsed < 0.29
    # 三查询存在共同并发窗口：max(start) < min(end)
    starts = [stamps[t][0] for t in stamps]
    ends = [stamps[t][1] for t in stamps]
    assert len(stamps) == 3 and max(starts) < min(ends)
    # 成功路径装配字段与串行一致
    bundle = out["case_evidence"]["c1"]
    assert bundle.case_info == {"case_id": "c1"}
    assert bundle.history_tickets == [] and bundle.history_complaints == []


def test_evidence_case_one_query_failure_returns_exception_info():
    """2.2：任一查询 MCP 异常 → 整节点返回 exception_info（形状同串行现状），不产出判责。"""
    from app.graph.nodes import evidence_case as mod

    async def _fail(server, tool, params, *, trace_id="", session_id=""):
        if tool == "query_history_tickets":
            raise MCPTimeoutError(server, tool, "timeout")
        return {"case_id": params["case_id"]} if tool == "query_case_info" else []

    mock = AsyncMock()
    mock.call = _fail
    with patch.object(mod, "get_mcp_client", return_value=mock):
        out = asyncio.run(mod.evidence_case_node(_state(case_ids=["c1"])))

    assert "case_evidence" not in out
    assert out["exception_info"]["error_type"] == "MCPTimeoutError"
    assert out["exception_info"]["node"] == "evidence_case"
    assert "timeout" in out["exception_info"]["message"]


def test_evidence_call_qc_concurrent_gather():
    """2.3：单 case 内两查询并发——总耗时≈最慢单次；ASR/QC 装配逻辑不变。"""
    import time

    from app.graph.nodes import evidence_call_qc as mod

    stamps: dict[str, list[float]] = {}

    async def _delayed_call(server, tool, params, *, trace_id="", session_id=""):
        stamps.setdefault(tool, []).append(time.monotonic())
        await asyncio.sleep(0.1)
        stamps[tool].append(time.monotonic())
        if tool == "query_call_asr":
            return [{"call_session_id": "cs-1", "call_duration": 60,
                     "asr_text_with_timestamp": [{"text": "你好我要投诉"}]}]
        return [{"qc_id": "qc-1", "violation_type": "辱骂", "snippet_text": "片段"}]

    mock = AsyncMock()
    mock.call = _delayed_call
    with patch.object(mod, "get_mcp_client", return_value=mock):
        start = time.monotonic()
        out = asyncio.run(mod.evidence_call_qc_node(_state(case_ids=["c1"])))
        elapsed = time.monotonic() - start

    # 耗时断言为防串行退化兜底（阈值贴近串行和 0.2）；主证据是窗口断言
    assert elapsed < 0.19
    starts = [stamps[t][0] for t in stamps]
    ends = [stamps[t][1] for t in stamps]
    assert len(stamps) == 2 and max(starts) < min(ends)
    # ASR 乱码/残缺置信度装配与 QC 违规装配原样保留
    bundle = out["case_evidence"]["c1"]
    assert len(bundle.call_evidence) == 1
    assert bundle.call_evidence[0]["content"] == "你好我要投诉"
    assert bundle.call_evidence[0]["is_corrupted"] is False
    assert len(bundle.qc_alerts) == 1
    assert bundle.qc_alerts[0]["evidence_id"] == "qc-1"


def test_evidence_call_qc_one_query_failure_returns_exception_info():
    """2.3：任一查询 MCP 异常 → 整节点返回 exception_info（形状同串行现状）。"""
    from app.graph.nodes import evidence_call_qc as mod

    async def _fail(server, tool, params, *, trace_id="", session_id=""):
        if tool == "query_call_asr":
            raise MCPTimeoutError(server, tool, "timeout")
        return []

    mock = AsyncMock()
    mock.call = _fail
    with patch.object(mod, "get_mcp_client", return_value=mock):
        out = asyncio.run(mod.evidence_call_qc_node(_state(case_ids=["c1"])))

    assert "case_evidence" not in out
    assert out["exception_info"]["error_type"] == "MCPTimeoutError"
    assert out["exception_info"]["node"] == "evidence_call_qc"
