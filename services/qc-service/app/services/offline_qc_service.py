"""整通离线质检编排。hits 唯一写入者。

管道 ⑧ 只调用 AlertService 算资格/内存告警，不在 _pipeline 内 PUBLISH。
六频道由 ReplayService 落库后发布。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.core.config import qc_settings
from app.events.publisher import QcEventPublisher
from app.observability import emit_check_started, emit_wait_text
from app.integrations.qc_session import CaseSnapshot, QCSession
from app.integrations.rag_client import RagClient
from app.repositories.demo_store import DemoTraceStore
from app.repositories.job_store import QcJobStore
from app.seeds.qc_rules_v1 import R14_KEYWORDS
from app.services.ac_filter import ACFilter, WordHit, get_shared_ac
from app.services.demo_trace import (
    call_qc_skip_event,
    elapsed_ms,
    make_trace_event,
    safe_append,
    safe_set_meta,
    utc_now,
)
from app.services.alert_service import AlertService
from app.services.dedup_ac import DedupACWindow
from app.services.llm_reviewer import LLMReviewer
from app.services.merge_hits import MergeHits
from app.services.r10 import PostMergeR10
from app.services.rule_engine import RuleEngine
from app.services.scoring_service import ScoreResult, ScoringService
from common.errors.codes import ERROR_MESSAGES, ErrorCode
from common.trace.middleware import get_trace_id

SUSPECTED_RULE_IDS = frozenset({"R11", "R12", "R13"})
CODE_OK = "0"
_LEGAL_CREDIT_MARKERS: tuple[str, ...] = R14_KEYWORDS + ("征信", "法院", "公安", "检察院", "律师")


def _has_legal_or_credit(text: str) -> bool:
    blob = text or ""
    return any(marker and marker in blob for marker in _LEGAL_CREDIT_MARKERS)


def searchable_agent_query(qc: QCSession, ac_hits: Sequence[WordHit]) -> str | None:
    """检索 query 必须来自本通真实 agent turn，禁止编造。"""
    agent_turns = [turn for turn in qc.turns if turn.speaker == "agent" and (turn.text or "").strip()]
    if not agent_turns:
        return None
    legal_turns = [turn for turn in agent_turns if _has_legal_or_credit(turn.text)]
    if legal_turns:
        chosen = max(legal_turns, key=lambda turn: len(turn.text or ""))
        return (chosen.text or "").strip()[:500]
    if ac_hits:
        chosen = max(agent_turns, key=lambda turn: len(turn.text or ""))
        return (chosen.text or "").strip()[:500]
    return None


def _set_standard_answer_hit(qc: QCSession, snippet: str) -> None:
    if qc.case_snapshot is None:
        qc.case_snapshot = CaseSnapshot()
    qc.case_snapshot.standard_answer_hit = snippet


def _stamp_kb_snippet(hits: Sequence[WordHit], snippet: str) -> None:
    for hit in hits:
        if not hit.kb_snippet:
            hit.kb_snippet = snippet


def _as_session(session: QCSession | Mapping[str, Any]) -> QCSession:
    if isinstance(session, QCSession):
        return session
    return QCSession.model_validate(dict(session))


def _text_ready(session: QCSession) -> bool:
    if not session.call_ended:
        return False
    if session.text_ready is False:
        return False
    if not session.turns:
        return False
    return True


def suspected_eligible(hits: Sequence[WordHit]) -> bool:
    return any(
        hit.speaker == "agent"
        and hit.severity == "high"
        and hit.rule_id in SUSPECTED_RULE_IDS
        for hit in hits
    )


def stop_call_eligible(hits: Sequence[WordHit]) -> bool:
    return any(
        hit.speaker == "agent" and hit.rule_id == "R13" and hit.score_eligible for hit in hits
    )


@dataclass
class QcRunResult:
    code: str = CODE_OK
    message: str = "ok"
    compliance_score: int | None = None
    risk_level: str | None = None
    grade: str | None = None
    qc_result: str | None = None
    qc_status: str | None = None
    job_status: str | None = None
    hits: list[WordHit] = field(default_factory=list)
    skipped_rules: list[str] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    suspected_eligible: bool = False
    stop_call_eligible: bool = False
    inserted: bool = False
    score: ScoreResult | None = None
    qc_record_id: str | None = None
    session_id: str | None = None
    case_id: str | None = None
    call_id: str | None = None
    started_monotonic: float | None = None

    def to_http(self) -> dict[str, Any]:
        """HTTP records：compliance_score + risk。不改库列名。"""
        return {
            "compliance_score": self.compliance_score,
            "risk": self.risk_level,
            "grade": self.grade,
            "qc_result": self.qc_result,
            "qc_status": self.qc_status,
        }

    def to_event(self) -> dict[str, Any]:
        """映射 score←compliance_score、risk←risk_level、grade，供落库后 qc.check.completed 使用；本方法本身不 redis.publish。"""
        return {
            "score": self.compliance_score,
            "risk": self.risk_level,
            "grade": self.grade,
        }

    def to_envelope(self) -> dict[str, Any]:
        """HTTP 200 信封：A2011 时 data 仍含 AC+规则分，不得 data=null。"""
        return {
            "code": self.code,
            "message": self.message,
            "data": {
                "compliance_score": self.compliance_score,
                "risk": self.risk_level,
                "grade": self.grade,
                "qc_result": self.qc_result,
                "qc_status": self.qc_status,
                "skipped_rules": self.skipped_rules,
            },
            "trace_id": get_trace_id() or "",
        }


class OfflineQcService:
    """顺序冻结：①AC ②窗口 ③规则(无 R10) ④LLM空 ⑤Merge ⑥R10 ⑦score ⑧agent+high 告警。"""

    def __init__(
        self,
        *,
        ac: ACFilter | None = None,
        window: DedupACWindow | None = None,
        rules: RuleEngine | None = None,
        llm: LLMReviewer | None = None,
        merger: MergeHits | None = None,
        r10: PostMergeR10 | None = None,
        scoring: ScoringService | None = None,
        alerter: AlertService | None = None,
        job_store: QcJobStore | None = None,
        publisher: QcEventPublisher | None = None,
        rag: RagClient | None = None,
        trace_store: DemoTraceStore | None = None,
    ) -> None:
        self.ac = ac or get_shared_ac()
        self.window = window or DedupACWindow()
        self.rules = rules or RuleEngine()
        self.job_store = job_store
        self.publisher = publisher or QcEventPublisher()
        self.llm = llm or LLMReviewer(job_store=job_store, publisher=self.publisher)
        self.merger = merger or MergeHits()
        self.r10 = r10 or PostMergeR10()
        self.scoring = scoring or ScoringService()
        self.alerter = alerter or AlertService()
        self.rag = rag
        self.trace_store = trace_store

    async def run(
        self,
        session: QCSession | Mapping[str, Any],
        *,
        call_records_exists: bool = True,
        text_retry_count: int = 0,
        persist: bool = False,
    ) -> QcRunResult:
        del persist
        qc = _as_session(session)
        if not call_records_exists:
            return QcRunResult(
                code=ErrorCode.A2003.value,
                message="需先落通话主数据",
                inserted=False,
                session_id=qc.session_id,
                case_id=qc.case_id,
                call_id=qc.call_id,
            )
        if not _text_ready(qc):
            max_retry = qc_settings.QC_TEXT_READY_RETRY_MAX
            if text_retry_count >= max_retry:
                return QcRunResult(
                    code=ErrorCode.A2003.value,
                    message=ERROR_MESSAGES[ErrorCode.A2003.value],
                    qc_status="PARTIAL",
                    inserted=False,
                    session_id=qc.session_id,
                    case_id=qc.case_id,
                    call_id=qc.call_id,
                )
            if self.job_store is not None:
                await self.job_store.set_wait_text(qc.session_id)
            emit_wait_text(session_id=qc.session_id)
            return QcRunResult(
                code=ErrorCode.A2003.value,
                message=ERROR_MESSAGES[ErrorCode.A2003.value],
                job_status="WAIT_TEXT",
                inserted=False,
                session_id=qc.session_id,
                case_id=qc.case_id,
                call_id=qc.call_id,
            )
        if self.job_store is not None:
            await self.job_store.set_running(qc.session_id)
        emit_check_started(session_id=qc.session_id, call_id=qc.call_id)
        started = time.perf_counter()
        try:
            result = await self._pipeline(qc)
        except Exception:
            return QcRunResult(
                code=ErrorCode.A2001.value,
                message=ErrorCode.A2001.value,
                qc_status="PARTIAL",
                inserted=False,
                session_id=qc.session_id,
                case_id=qc.case_id,
                call_id=qc.call_id,
                started_monotonic=started,
            )
        result.started_monotonic = started
        return result

    async def _trace(
        self,
        session_id: str,
        *,
        category: str,
        name: str,
        status: str,
        summary: str,
        duration_ms: int = 0,
        started_at=None,
    ) -> None:
        await safe_append(
            self.trace_store,
            session_id,
            make_trace_event(
                category=category,
                name=name,
                status=status,
                summary=summary,
                duration_ms=duration_ms,
                started_at=started_at,
            ),
        )

    async def _pipeline(self, qc: QCSession) -> QcRunResult:
        await safe_append(self.trace_store, qc.session_id, call_qc_skip_event())
        t0 = time.perf_counter()
        started = utc_now()
        ac_hits = self.window.apply(self.ac.match_turns(qc.turns))
        await self._trace(
            qc.session_id,
            category="ac",
            name="ac_filter",
            status="ok",
            summary=f"ac_hits={len(ac_hits)}",
            duration_ms=elapsed_ms(t0),
            started_at=started,
        )
        t0 = time.perf_counter()
        started = utc_now()
        snippet = await self._maybe_kb_search(qc, ac_hits)
        if not qc_settings.QC_R14_RAG_ENABLED:
            search_status, search_summary = "skipped", "QC_R14_RAG_ENABLED=false"
        elif snippet:
            search_status, search_summary = "ok", "kb_snippet filled"
        elif not searchable_agent_query(qc, ac_hits):
            search_status, search_summary = "skipped", "no searchable agent turn"
        else:
            search_status, search_summary = "empty", "search empty or failed; fallback keywords"
        await self._trace(
            qc.session_id,
            category="search",
            name="kb_search",
            status=search_status,
            summary=search_summary,
            duration_ms=elapsed_ms(t0),
            started_at=started,
        )
        t0 = time.perf_counter()
        started = utc_now()
        rule_result = self.rules.run(qc)
        await self._trace(
            qc.session_id,
            category="rules",
            name="rules_r01_r15",
            status="ok",
            summary=f"rule_hits={len(rule_result.hits)} skipped={len(rule_result.skipped_rules)}",
            duration_ms=elapsed_ms(t0),
            started_at=started,
        )
        t0 = time.perf_counter()
        started = utc_now()
        llm_hits = await self.llm.review(qc.turns, session_id=qc.session_id)
        if getattr(self.llm, "degraded", False):
            llm_status, llm_summary = "degraded", f"llm_hits={len(llm_hits)} degraded"
        elif qc_settings.QC_MOCK_LLM:
            llm_status, llm_summary = "skipped", f"QC_MOCK_LLM=true llm_hits={len(llm_hits)}"
        else:
            llm_status, llm_summary = "ok", f"llm_hits={len(llm_hits)}"
        await self._trace(
            qc.session_id,
            category="llm",
            name="llm_review",
            status=llm_status,
            summary=llm_summary,
            duration_ms=elapsed_ms(t0),
            started_at=started,
        )
        run_id = getattr(self.llm, "last_langsmith_run_id", None)
        if run_id:
            await safe_set_meta(self.trace_store, qc.session_id, langsmith_run_id=run_id)
        merged = self.merger.apply(ac_hits, rule_result.hits, llm_hits)
        skipped = list(rule_result.skipped_rules)
        r10 = self.r10.apply(merged, qc.result)
        if r10.skipped and "R10" not in skipped:
            skipped.append("R10")
        if r10.hit is not None:
            merged = [*merged, r10.hit]
        if snippet:
            _stamp_kb_snippet(merged, snippet)
        t0 = time.perf_counter()
        started = utc_now()
        scored = self.scoring.score(merged)
        await self._trace(
            qc.session_id,
            category="score",
            name="scoring",
            status="ok",
            summary=f"compliance_score={scored.compliance_score} qc_status={scored.qc_status}",
            duration_ms=elapsed_ms(t0),
            started_at=started,
        )
        alerts = self.alerter.on_high_risk(merged)
        code = ErrorCode.A2011.value if getattr(self.llm, "degraded", False) else CODE_OK
        message = ErrorCode.A2011.value if code == ErrorCode.A2011.value else "ok"
        return QcRunResult(
            code=code,
            message=message,
            compliance_score=scored.compliance_score,
            risk_level=scored.risk_level,
            grade=scored.grade,
            qc_result=scored.qc_result,
            qc_status=scored.qc_status,
            hits=merged,
            skipped_rules=skipped,
            alerts=alerts,
            suspected_eligible=suspected_eligible(merged),
            stop_call_eligible=stop_call_eligible(merged),
            inserted=False,
            score=scored,
            session_id=qc.session_id,
            case_id=qc.case_id,
            call_id=qc.call_id,
        )

    async def _maybe_kb_search(self, qc: QCSession, ac_hits: Sequence[WordHit]) -> str | None:
        if not qc_settings.QC_R14_RAG_ENABLED:
            return None
        query = searchable_agent_query(qc, ac_hits)
        if not query:
            return None
        rag = self.rag
        if rag is None:
            from app.deps import get_rag_client

            rag = get_rag_client()
            self.rag = rag
        try:
            snippet = await rag.search(query)
        except Exception:
            return None
        if not snippet:
            return None
        _set_standard_answer_hit(qc, snippet)
        return snippet
