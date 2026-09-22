"""LLM 降级（9.2a）—— 3 场景。

1. LLM 判责失败 → RuleBasedScriptEngine（A5004）
2. 产出物生成失败 → 模板填充答辩初稿（A5010）
3. RAG 检索失败 → 规则兜底不引用法规（A5005）
降级标记写入 state.exception_info。

另含 graph 6 节点 LLM 降级统一 catch 模板（spec: complaint-service/graph-nodes）：
TimeoutError / CircuitBreakerOpenError / LLMCallError → [规则兜底] 草稿 +
exception_info + pending_review + csmp.degrade.triggered 事件，不抛异常。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from common.errors.codes import ErrorCode

if TYPE_CHECKING:  # 仅类型标注，避免运行时环 import
    from app.models.state import ComplaintSessionState

logger = structlog.get_logger()

_TEMPLATE_RESPONSE = "经核实，您反馈的问题我们正在处理，将尽快与您联系。"


class DegradeLLM:
    @staticmethod
    def _exc(code: str, reason: str) -> dict:
        return {"code": code, "reason": reason,
                "ts": datetime.now(timezone.utc).isoformat()}

    def handle_judge_failure(self, state: dict | None = None) -> dict:
        """LLM 判责失败 → RuleBased 最小判责。"""
        out = {
            "verdict": "pending",
            "confidence": 0.0,
            "response_draft": _TEMPLATE_RESPONSE,
            "exception_info": [self._exc(ErrorCode.A5004.value, "LLM 判责失败，RuleBased 兜底")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out

    def handle_output_failure(self, state: dict | None = None) -> dict:
        """产出物失败 → 模板填充。"""
        out = {
            "response_draft": _TEMPLATE_RESPONSE,
            "exception_info": [self._exc(ErrorCode.A5010.value, "产出物生成失败，模板填充")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out

    def handle_rag_failure(self, state: dict | None = None) -> dict:
        """RAG 失败 → 规则兜底不引用法规。"""
        out = {
            "verdict": "pending",
            "regulation_refs": [],
            "exception_info": [self._exc(ErrorCode.A5005.value, "RAG 检索失败，规则兜底")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out


# 全局单例
degrade_llm = DegradeLLM()


# ---------------------------------------------------------------------------
# graph 6 节点 LLM 降级统一 catch 模板（spec: complaint-service/graph-nodes）
# ---------------------------------------------------------------------------

_RULE_FALLBACK_PREFIX = "[规则兜底]"
_RULE_ENGINE: Any | None = None  # ComplaintRuleEngine 惰性单例


def _get_rule_engine() -> Any:
    global _RULE_ENGINE
    if _RULE_ENGINE is None:
        from app.llm.fallback import ComplaintRuleEngine

        _RULE_ENGINE = ComplaintRuleEngine()
    return _RULE_ENGINE


def map_llm_exc_type(exc: BaseException) -> str:
    """异常 → exception_info.error_type（值域 {timeout, circuit_open, llm_degraded}）。"""
    from app.llm.provider import CircuitBreakerOpenError

    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, CircuitBreakerOpenError):
        return "circuit_open"
    try:
        from common.mcp.reliability import MCPCircuitOpenError

        if isinstance(exc, MCPCircuitOpenError):
            return "circuit_open"
    except ImportError:  # pragma: no cover
        pass
    return "llm_degraded"


def _qc_violations_of(state: "ComplaintSessionState") -> list[dict]:
    """收集各案件质检告警（判责规则兜底输入）。"""
    out: list[dict] = []
    for bundle in (state.case_evidence or {}).values():
        alerts = (
            getattr(bundle, "qc_alerts", None)
            or (bundle.get("qc_alerts") if isinstance(bundle, dict) else None)
            or []
        )
        out.extend(alerts)
    return out


async def node_degrade_fallback(
    node: str, state: "ComplaintSessionState", exc: BaseException
) -> dict:
    """6 个 graph 节点 LLM 降级统一模板（catch 块调用，绝不抛异常）。

    - ComplaintRuleEngine 产出带 [规则兜底] 前缀的规则草稿（输出不为空）；
    - DegradeLLM 记录该节点对应降级场景码（A5004/A5010/A5005）入事件 payload；
    - 设 state.exception_info（type ∈ {timeout, circuit_open, llm_degraded}）；
    - 设 state.task_status=pending_review；
    - 发 csmp.degrade.triggered 事件（code=A5017，带 trace_id/reason/node）；
    - 返回 LangGraph update dict，异常不冒泡。
    """
    from app.models.state import (
        ComplianceOpinion,
        ExceptionInfo,
        FactTimeline,
        JudgmentPackage,
        ReflectionReport,
        TaskStatus,
        UserDemandAnalysis,
        VerdictDraft,
        VerdictType,
    )
    from common.resilience.hooks import on_event

    exc_type = map_llm_exc_type(exc)
    engine = _get_rule_engine()
    update: dict = {
        "exception_info": ExceptionInfo(
            error_type=exc_type, message=str(exc)[:300], node=node
        ).model_dump(),
        "task_status": TaskStatus.pending_review.value,
    }

    degrade_record: dict | None = None
    if node == "verdict":
        degrade_record = degrade_llm.handle_judge_failure()
        rule = engine.generate_verdict_draft(qc_violations=_qc_violations_of(state))
        update["verdict_draft"] = VerdictDraft(
            verdict=VerdictType(rule["verdict"]),
            confidence=float(rule["confidence"]),
            reasoning=rule["reasoning"],
            conflict_highlight=True,
        )
    elif node == "fact":
        rule = engine.generate_fact_timeline(events=[])
        update["fact_timeline"] = FactTimeline(
            timeline=rule["events"], summary=rule["summary"]
        )
    elif node == "reflection":
        from app.utils.evidence_helper import _collect_all_evidence

        rule = engine.generate_reflection_report(
            evidence_items=_collect_all_evidence(state.model_dump())
        )
        update["reflection_report"] = ReflectionReport(
            evidence_sufficient=bool(rule["sufficient"]),
            missing_evidence=[],
            contradictions=[],
            suggestion=rule["summary"],
        )
        update["loop_count"] = state.loop_count + 1
    elif node == "compliance":
        degrade_record = degrade_llm.handle_rag_failure()
        rule = engine.generate_compliance_opinion()
        update["compliance_opinion"] = ComplianceOpinion(
            overall_compliance="uncertain",
            regulation_refs=list(rule["regulation_refs"]),
            opinions=[],
            details=rule["compliance_conclusion"],
        )
    elif node == "user_demand":
        rule = engine.generate_user_demand(text=state.complaint_text)
        update["user_demand"] = UserDemandAnalysis(
            demand_type=f"{_RULE_FALLBACK_PREFIX} {rule['demand_type']}",
            emotion_level="unknown",
            secondary_complaint_risk="unknown",
        )
    elif node == "generate_package":
        degrade_record = degrade_llm.handle_output_failure()
        rule = engine.generate_judgment_package()
        update["judgment_package"] = JudgmentPackage(
            evidence_chain=[],
            response_draft=rule["response_draft"],
            judgment_report=rule["response_draft"],
            rag_source_ids=[],
            regulation_refs=[],
        )
    else:
        logger.warning("node_degrade_unknown_node", node=node)

    payload: dict[str, Any] = {
        "code": "A5017",
        "reason": exc_type,
        "node": node,
        "trace_id": state.trace_id,
    }
    if degrade_record:
        entries = degrade_record.get("exception_info") or []
        if entries:
            payload["degrade_code"] = entries[0].get("code", "")

    logger.warning(
        "node_llm_degrade",
        node=node,
        exc_type=exc_type,
        error=str(exc)[:200],
        trace_id=state.trace_id,
    )
    await on_event("csmp.degrade.triggered", payload)
    return update
