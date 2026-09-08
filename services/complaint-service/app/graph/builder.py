"""LangGraph 整图编译。

注册批次5 九节点 + 批次6 五节点，配置条件边与两个扇出节点的并行边。
human_review 使用 interrupt 作为人工断点，无硬边到 sample_backflow。
"""
from __future__ import annotations

from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.edges import (
    should_proceed_after_review,
    should_proceed_after_verdict,
    should_retry_or_proceed,
)
from app.graph.nodes.compliance import compliance_node
from app.graph.nodes.evidence_call_qc import evidence_call_qc_node
from app.graph.nodes.evidence_case import evidence_case_node
from app.graph.nodes.evidence_re_gather import evidence_re_gather_node
from app.graph.nodes.fact import fact_node
from app.graph.nodes.generate_package import generate_package_node
from app.graph.nodes.human_review import human_review_node
from app.graph.nodes.judgment_fanout import judgment_fanout_node
from app.graph.nodes.orchestrator import orchestrator_node
from app.graph.nodes.reflection import reflection_node
from app.graph.nodes.sample_backflow import sample_backflow_node
from app.graph.nodes.user_demand import user_demand_node
from app.graph.nodes.verdict import verdict_node
from app.models.state import ComplaintSessionState

# 节点名常量
ORCH = "orchestrator"
EVI_CASE = "evidence_case"
EVI_QC = "evidence_call_qc"
RE_GATHER = "evidence_re_gather"
REFLECT = "reflection"
JUDGE_FANOUT = "judgment_fanout"
FACT = "fact"
COMPLIANCE = "compliance"
USER_DEMAND = "user_demand"
VERDICT = "verdict"
GEN_PKG = "generate_package"
HUMAN_REVIEW = "human_review"
BACKFLOW = "sample_backflow"


def build_complaint_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """编译投诉研判 LangGraph。"""
    builder = StateGraph(ComplaintSessionState)

    # ── 注册节点 ──
    builder.add_node(ORCH, orchestrator_node)
    builder.add_node(EVI_CASE, evidence_case_node)
    builder.add_node(EVI_QC, evidence_call_qc_node)
    builder.add_node(RE_GATHER, evidence_re_gather_node)
    builder.add_node(REFLECT, reflection_node)
    builder.add_node(JUDGE_FANOUT, judgment_fanout_node)
    builder.add_node(FACT, fact_node)
    builder.add_node(COMPLIANCE, compliance_node)
    builder.add_node(USER_DEMAND, user_demand_node)
    builder.add_node(VERDICT, verdict_node)
    builder.add_node(GEN_PKG, generate_package_node)
    builder.add_node(HUMAN_REVIEW, human_review_node)
    builder.add_node(BACKFLOW, sample_backflow_node)

    # ── 入口 ──
    builder.add_edge(START, ORCH)

    # orchestrator → 双证据扇出
    builder.add_edge(ORCH, EVI_CASE)
    builder.add_edge(ORCH, EVI_QC)

    # 双证据汇入 reflection
    builder.add_edge(EVI_CASE, REFLECT)
    builder.add_edge(EVI_QC, REFLECT)

    # reflection → 条件边
    builder.add_conditional_edges(
        REFLECT,
        should_retry_or_proceed,
        {
            "to_manual": HUMAN_REVIEW,
            "to_re_gather": RE_GATHER,
            "to_judgment": JUDGE_FANOUT,
        },
    )

    # 4.14 evidence_re_gather → 双证据扇出（2 条边）
    builder.add_edge(RE_GATHER, EVI_CASE)
    builder.add_edge(RE_GATHER, EVI_QC)

    # 4.13 judgment_fanout → 三节点并行（3 条边）
    builder.add_edge(JUDGE_FANOUT, FACT)
    builder.add_edge(JUDGE_FANOUT, COMPLIANCE)
    builder.add_edge(JUDGE_FANOUT, USER_DEMAND)

    # 三节点汇入 verdict
    builder.add_edge(FACT, VERDICT)
    builder.add_edge(COMPLIANCE, VERDICT)
    builder.add_edge(USER_DEMAND, VERDICT)

    # verdict → 条件边
    builder.add_conditional_edges(
        VERDICT,
        should_proceed_after_verdict,
        {
            "to_review": HUMAN_REVIEW,
            "to_package": GEN_PKG,
        },
    )

    # generate_package → human_review
    builder.add_edge(GEN_PKG, HUMAN_REVIEW)

    # human_review → 条件边（FATAL-1：无硬边到 sample_backflow）
    builder.add_conditional_edges(
        HUMAN_REVIEW,
        should_proceed_after_review,
        {
            "to_re_gather": RE_GATHER,
            "to_manual": HUMAN_REVIEW,
            "to_senior_review": HUMAN_REVIEW,
            "to_backflow": BACKFLOW,
        },
    )

    # sample_backflow → END
    builder.add_edge(BACKFLOW, END)

    return builder.compile(checkpointer=checkpointer)
