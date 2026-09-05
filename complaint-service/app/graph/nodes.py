from app.models.state import (
    ComplaintSessionState, TaskStatus, ExceptionInfo,
    CaseEvidenceBundle, EvidenceItem, ReflectionReport, FactTimeline,
    ComplianceOpinion, UserDemandAnalysis, VerdictDraft, JudgmentPackage,
)
from app.mcp.client import mcp_client
from app.llm.provider import llm_provider
from app.prompts.loader import load_prompt


def _collect_all_evidence(state: ComplaintSessionState) -> list[EvidenceItem]:
    all_items = list(state.cross_case_evidence) + list(state.historical_complaint_evidence)
    for bundle in state.case_evidence.values():
        all_items.extend(bundle.call_evidence)
        all_items.extend(bundle.history_tickets)
        all_items.extend(bundle.history_complaints)
        all_items.extend(bundle.qc_alerts)
    return all_items


async def orchestrator_node(state: ComplaintSessionState) -> ComplaintSessionState:
    if state.exception_info.has_exception:
        state.task_status = TaskStatus.pending_review
        return state
    if state.total_tokens_consumed > state.max_token_threshold:
        state.exception_info = ExceptionInfo(
            has_exception=True, exception_type="token_exceeded",
            exception_detail=f"Token消耗{state.total_tokens_consumed}超阈值{state.max_token_threshold}",
            degrade_action="转人工处理",
        )
        state.task_status = TaskStatus.pending_review
        return state
    state.task_status = TaskStatus.evidence_gathering
    return state


async def evidence_case_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    try:
        for case_id in state.case_ids:
            case_data = await mcp_client.call(
                server="business-ticket", tool="query_case_info",
                params={"case_id": case_id, "session_id": state.session_id},
            )
            history_tickets = await mcp_client.call(
                server="business-ticket", tool="query_history_tickets",
                params={"case_id": case_id, "session_id": state.session_id},
            )
            history_complaints = await mcp_client.call(
                server="business-ticket", tool="query_history_complaints",
                params={"case_id": case_id, "session_id": state.session_id},
            )
            state.case_evidence[case_id] = CaseEvidenceBundle(
                case_id=case_id, case_info=case_data,
                history_tickets=history_tickets, history_complaints=history_complaints,
            )
        if state.user_id:
            hist_complaints = await mcp_client.call(
                server="business-ticket", tool="query_user_history_complaints",
                params={"user_id": state.user_id, "days": 90, "session_id": state.session_id},
            )
            state.historical_complaint_evidence = hist_complaints
    except Exception as e:
        state.exception_info = ExceptionInfo(
            has_exception=True, exception_type="mcp_timeout",
            exception_detail=f"业务工单MCP异常: {e}", degrade_action="标记证据缺失",
        )
    return state


async def evidence_call_qc_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    try:
        for case_id in state.case_ids:
            call_evidence = await mcp_client.call(
                server="call-qc", tool="query_call_asr",
                params={"case_id": case_id, "session_id": state.session_id},
            )
            qc_alerts = await mcp_client.call(
                server="call-qc", tool="query_qc_violations",
                params={"case_id": case_id, "session_id": state.session_id},
            )
            state.case_evidence[case_id] = CaseEvidenceBundle(
                case_id=case_id, call_evidence=call_evidence, qc_alerts=qc_alerts,
            )
    except Exception as e:
        state.exception_info = ExceptionInfo(
            has_exception=True, exception_type="mcp_timeout",
            exception_detail=f"通话质检MCP异常: {e}", degrade_action="标记证据缺失",
        )
    return state


async def evidence_re_gather_fanout_node(state: ComplaintSessionState) -> ComplaintSessionState:
    state.task_status = TaskStatus.evidence_gathering
    state.exception_info = ExceptionInfo()
    return state


async def reflection_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    all_evidence = _collect_all_evidence(state)
    report = await llm_provider.invoke(
        prompt=load_prompt("complaint_reflection", evidence=all_evidence, case_ids=state.case_ids),
        schema=ReflectionReport, trace_id=state.trace_id, session_id=state.session_id,
    )
    state.reflection_report = report
    state.loop_count += 1
    return state


async def judgment_fanout_node(state: ComplaintSessionState) -> ComplaintSessionState:
    state.task_status = TaskStatus.judging
    return state


async def fact_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    all_evidence = _collect_all_evidence(state)
    result = await llm_provider.invoke(
        prompt=load_prompt("complaint_fact_timeline", evidence=all_evidence, case_evidence=state.case_evidence),
        schema=FactTimeline, trace_id=state.trace_id, session_id=state.session_id,
    )
    state.fact_timeline = result
    return state


async def compliance_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    try:
        rag_result = await mcp_client.call(
            server="rag-kb", tool="search_regulations",
            params={"query": state.complaint_text, "session_id": state.session_id},
        )
    except Exception:
        rag_result = []
        state.exception_info = ExceptionInfo(
            has_exception=True, exception_type="mcp_timeout",
            exception_detail="RAG知识库MCP超时",
            degrade_action="降级为规则兜底判责，不引用法规",
        )
    all_evidence = _collect_all_evidence(state)
    result = await llm_provider.invoke(
        prompt=load_prompt("complaint_compliance", evidence=all_evidence, rag_results=rag_result),
        schema=ComplianceOpinion, trace_id=state.trace_id, session_id=state.session_id,
    )
    state.compliance_opinion = result
    return state


async def user_demand_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    all_evidence = _collect_all_evidence(state)
    result = await llm_provider.invoke(
        prompt=load_prompt("complaint_user_demand", complaint_text=state.complaint_text, evidence=all_evidence),
        schema=UserDemandAnalysis, trace_id=state.trace_id, session_id=state.session_id,
    )
    state.user_demand = result
    return state


async def verdict_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    if state.fact_timeline is None or state.compliance_opinion is None or state.user_demand is None:
        state.exception_info = ExceptionInfo(
            has_exception=True, exception_type="llm_degraded",
            exception_detail="研判前置节点输出缺失",
            degrade_action="转人工处理",
        )
        state.task_status = TaskStatus.pending_review
        return state
    result = await llm_provider.invoke(
        prompt=load_prompt(
            "complaint_verdict",
            fact_timeline=state.fact_timeline,
            compliance_opinion=state.compliance_opinion,
            user_demand=state.user_demand, case_ids=state.case_ids,
        ),
        schema=VerdictDraft, trace_id=state.trace_id, session_id=state.session_id,
    )
    if result.confidence < 0.7:
        result.conflict_highlight = True
    state.verdict_draft = result
    state.task_status = TaskStatus.draft_generated
    return state


async def generate_package_agent_node(state: ComplaintSessionState) -> ComplaintSessionState:
    try:
        rag_result = await mcp_client.call(
            server="rag-kb", tool="search_response_templates",
            params={"verdict": state.verdict_draft.verdict, "session_id": state.session_id},
        )
    except Exception:
        rag_result = []
        state.exception_info = ExceptionInfo(
            has_exception=True, exception_type="mcp_timeout",
            exception_detail="RAG答辩模板MCP超时",
            degrade_action="降级为模板填充",
        )
    result = await llm_provider.invoke(
        prompt=load_prompt(
            "complaint_generate_package",
            verdict_draft=state.verdict_draft, fact_timeline=state.fact_timeline,
            compliance_opinion=state.compliance_opinion, user_demand=state.user_demand,
            rag_templates=rag_result, case_evidence=state.case_evidence,
        ),
        schema=JudgmentPackage, trace_id=state.trace_id, session_id=state.session_id,
    )
    state.judgment_package = result
    return state


async def human_review_node(state: ComplaintSessionState) -> ComplaintSessionState:
    from langgraph.types import interrupt
    review_result = interrupt({
        "session_id": state.session_id,
        "judgment_package": state.judgment_package,
    })
    from app.models.state import HumanReviewResult
    state.human_review = HumanReviewResult(**review_result)
    action = state.human_review.review_action
    if action == "approve":
        state.task_status = TaskStatus.resolved
    elif action in ("modify_verdict", "modify_response"):
        state.task_status = TaskStatus.resolved
    elif action == "reject_rejudge":
        state.task_status = TaskStatus.evidence_gathering
    elif action == "escalate":
        state.task_status = TaskStatus.pending_senior_review
    return state


async def sample_backflow_node(state: ComplaintSessionState) -> ComplaintSessionState:
    await mcp_client.call(
        server="data-platform-writeback", tool="write_session",
        params=state.model_dump(),
    )
    if state.human_review and state.human_review.ai_human_diff:
        await mcp_client.call(
            server="data-platform-writeback", tool="write_human_review_truth",
            params={"complaint_id": state.complaint_id, "human_review": state.human_review.model_dump()},
        )
    state.task_status = TaskStatus.archived
    return state
