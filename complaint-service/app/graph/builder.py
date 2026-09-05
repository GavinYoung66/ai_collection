from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import asyncpg

from app.models.state import ComplaintSessionState
from app.graph.nodes import (
    orchestrator_node,
    evidence_case_agent_node,
    evidence_call_qc_agent_node,
    evidence_re_gather_fanout_node,
    reflection_agent_node,
    judgment_fanout_node,
    fact_agent_node,
    compliance_agent_node,
    user_demand_agent_node,
    verdict_agent_node,
    generate_package_agent_node,
    human_review_node,
    sample_backflow_node,
)
from app.graph.edges import should_retry_or_proceed, should_proceed_after_review


async def init_checkpointer(database_url: str):
    pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=5,
        max_size=20,
        command_timeout=30,
    )
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    return checkpointer


def build_complaint_graph(checkpointer=None):
    graph = StateGraph(ComplaintSessionState)

    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("evidence_case", evidence_case_agent_node)
    graph.add_node("evidence_call_qc", evidence_call_qc_agent_node)
    graph.add_node("evidence_re_gather", evidence_re_gather_fanout_node)
    graph.add_node("reflection", reflection_agent_node)
    graph.add_node("judgment_fanout", judgment_fanout_node)
    graph.add_node("fact", fact_agent_node)
    graph.add_node("compliance", compliance_agent_node)
    graph.add_node("user_demand", user_demand_agent_node)
    graph.add_node("verdict", verdict_agent_node)
    graph.add_node("generate_package", generate_package_agent_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("sample_backflow", sample_backflow_node)

    graph.set_entry_point("orchestrator")

    graph.add_edge("orchestrator", "evidence_case")
    graph.add_edge("orchestrator", "evidence_call_qc")

    graph.add_edge("evidence_re_gather", "evidence_case")
    graph.add_edge("evidence_re_gather", "evidence_call_qc")

    graph.add_edge("evidence_case", "reflection")
    graph.add_edge("evidence_call_qc", "reflection")

    graph.add_conditional_edges(
        "reflection",
        should_retry_or_proceed,
        {
            "to_re_gather": "evidence_re_gather",
            "to_manual": "sample_backflow",
            "to_judgment": "judgment_fanout",
        },
    )

    graph.add_edge("judgment_fanout", "fact")
    graph.add_edge("judgment_fanout", "compliance")
    graph.add_edge("judgment_fanout", "user_demand")

    graph.add_edge("fact", "verdict")
    graph.add_edge("compliance", "verdict")
    graph.add_edge("user_demand", "verdict")
    graph.add_edge("verdict", "generate_package")
    graph.add_edge("generate_package", "human_review")

    graph.add_conditional_edges(
        "human_review",
        should_proceed_after_review,
        {
            "to_re_gather": "evidence_re_gather",
            "to_senior_review": "sample_backflow",
            "to_backflow": "sample_backflow",
        },
    )

    graph.add_edge("sample_backflow", END)

    if checkpointer:
        graph.compile(checkpointer=checkpointer)
    else:
        graph.compile()
    return graph
