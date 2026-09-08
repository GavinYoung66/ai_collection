"""reflection 反思 Agent 节点。

收集全量证据，调用 LLM 输出反思报告，loop_count += 1。
不自行发起取证循环，流转权交还调度。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_llm_provider
from app.models.state import ComplaintSessionState, ReflectionReport
from app.prompt.loader import load_prompt
from app.utils.evidence_helper import _collect_all_evidence

logger = structlog.get_logger()


async def reflection_node(state: ComplaintSessionState) -> dict:
    """反思节点：产出 ReflectionReport 并递增 loop_count。"""
    evidence = _collect_all_evidence(state.model_dump())
    prompt = load_prompt(
        "complaint_reflection",
        evidence_text=str(evidence)[:4000],
        loop_count=state.loop_count,
        evidence_count=len(evidence),
    )
    llm = get_llm_provider()
    report: ReflectionReport = await llm.invoke(
        prompt,
        schema=ReflectionReport,
        trace_id=state.trace_id,
        session_id=state.session_id,
        agent_role="reflection",
    )
    # 真实模型偶发误判「证据不足」：本地已采到通话+案件信息时强制充分，保证后续 Agent 能干活
    has_call = any(
        (getattr(b, "call_evidence", None) or (b.get("call_evidence") if isinstance(b, dict) else None))
        for b in (state.case_evidence or {}).values()
    )
    has_case_info = any(
        (getattr(b, "case_info", None) or (b.get("case_info") if isinstance(b, dict) else None))
        for b in (state.case_evidence or {}).values()
    )
    if (
        hasattr(report, "evidence_sufficient")
        and not report.evidence_sufficient
        and has_call
        and has_case_info
        and len(evidence) >= 2
    ):
        logger.info(
            "reflection_force_sufficient",
            evidence_count=len(evidence),
            missing=getattr(report, "missing_evidence", None),
        )
        report.evidence_sufficient = True
        report.missing_evidence = []
        tip = "（系统：已采到案件信息与通话证据，按充分处理）"
        report.suggestion = f"{(report.suggestion or '').strip()} {tip}".strip()

    return {"reflection_report": report, "loop_count": state.loop_count + 1}
