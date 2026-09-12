"""离线 replay：进程内跑 OfflineQcService，并由 HTTP 层负责落库。落库后 PUBLISH 六频道。"""
from __future__ import annotations

import time
from typing import Any, Mapping
from uuid import UUID

from app.events.publisher import event_call_id
from app.integrations.dp_client import DpClient
from app.integrations.qc_session import QCSession
from app.models.qc import QcRecord
from app.observability import emit_check_completed
from app.repositories.alert_repo import AlertRepo
from app.repositories.demo_store import DemoTraceStore
from app.repositories.job_store import QcJobStore
from app.repositories.quality_repo import QualityRepo
from app.services.ac_filter import WordHit
from app.services.demo_trace import elapsed_ms, make_trace_event, safe_append, safe_set_meta, utc_now
from app.services.offline_qc_service import CODE_OK, SUSPECTED_RULE_IDS, OfflineQcService, QcRunResult
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.security.mask import mask_text

NO_MASTER_MSG = "需先落通话主数据"


def _as_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def hit_to_http(hit: WordHit) -> dict[str, Any]:
    return {
        "turn_id": hit.turn_id,
        "rule_id": hit.rule_id,
        "word_id": hit.word_id,
        "severity": hit.severity,
        "redline_tag": hit.redline_tag,
        "speaker": hit.speaker,
        "score_eligible": bool(hit.score_eligible),
        "evidence_text": mask_text(hit.evidence_text or ""),
        "source": hit.source,
        "word": hit.word or None,
        "kb_snippet": hit.kb_snippet,
    }


def _result_from_record(row: QcRecord, qc: QCSession) -> QcRunResult:
    """幂等命中：回已有终态行，不重跑管道、不 INSERT。"""
    return QcRunResult(
        code=CODE_OK,
        message="ok",
        compliance_score=row.compliance_score,
        risk_level=row.risk_level,
        grade=row.grade,
        qc_result=row.qc_result,
        qc_status=row.qc_status,
        inserted=False,
        qc_record_id=str(row.id) if row.id is not None else None,
        session_id=qc.session_id,
        case_id=qc.case_id,
        call_id=qc.call_id,
    )


def run_result_to_http(result: QcRunResult) -> dict[str, Any]:
    """QcRunResult HTTP 字段。不含 skipped_rules、不含 score 作合规分。"""
    return {
        "qc_record_id": result.qc_record_id,
        "session_id": result.session_id,
        "case_id": result.case_id,
        "call_id": result.call_id,
        "compliance_score": result.compliance_score,
        "risk": result.risk_level,
        "grade": result.grade,
        "qc_result": result.qc_result,
        "qc_status": result.qc_status,
        "hits": [hit_to_http(hit) for hit in result.hits],
    }


class ReplayService:
    def __init__(
        self,
        offline: OfflineQcService | None = None,
        *,
        job_store: QcJobStore | None = None,
        quality_repo: QualityRepo | None = None,
        dp_client: DpClient | None = None,
        trace_store: DemoTraceStore | None = None,
    ) -> None:
        if offline is None:
            self.offline = OfflineQcService(job_store=job_store, trace_store=trace_store)
        else:
            if job_store is not None:
                if offline.job_store is None:
                    offline.job_store = job_store
                elif offline.job_store is not job_store:
                    raise ValueError("ReplayService must reuse OfflineQcService.job_store")
            if trace_store is not None and getattr(offline, "trace_store", None) is None:
                offline.trace_store = trace_store
            self.offline = offline
        self.quality_repo = quality_repo
        self.dp_client = dp_client

    @property
    def job_store(self) -> QcJobStore | None:
        return self.offline.job_store

    async def run(
        self,
        session: QCSession | Mapping[str, Any],
        *,
        call_records_exists: bool = True,
        text_retry_count: int = 0,
    ) -> QcRunResult:
        return await self.offline.run(
            session,
            call_records_exists=call_records_exists,
            text_retry_count=text_retry_count,
            persist=False,
        )

    async def _assert_force_rerun(self, session_id: str, *, force: bool) -> None:
        if self.quality_repo is None:
            return
        existing = await self.quality_repo.get_by_session_id(session_id)
        if existing is not None and existing.qc_status == "REVIEWED" and not force:
            raise BizException(ErrorCode.A2002)

    async def _persist(self, result: QcRunResult, qc: QCSession) -> QcRunResult:
        if self.quality_repo is None:
            return result
        if result.job_status == "WAIT_TEXT":
            return result
        if result.code == ErrorCode.A2003.value and result.qc_status != "PARTIAL":
            return result
        existing = await self.quality_repo.get_by_session_id(qc.session_id)
        score = result.score
        high = score.high_count if score else 0
        med = score.med_count if score else 0
        low = score.low_count if score else 0
        compliance = result.compliance_score if result.compliance_score is not None else 0
        risk = result.risk_level or "low"
        qc_result = result.qc_result or "compliant"
        qc_status = result.qc_status or "DONE"
        case_id = _as_uuid(qc.case_id)
        if existing is None:
            row = await self.quality_repo.insert_record(
                session_id=qc.session_id,
                compliance_score=compliance,
                risk_level=risk,
                qc_result=qc_result,
                qc_status=qc_status,
                grade=result.grade,
                case_id=case_id,
                high_count=high,
                med_count=med,
                low_count=low,
            )
        else:
            existing.compliance_score = compliance
            existing.risk_level = risk
            existing.qc_result = qc_result
            existing.qc_status = qc_status
            existing.grade = result.grade
            existing.high_count = high
            existing.med_count = med
            existing.low_count = low
            row = existing
            await self.quality_repo.session.flush()
        violation_ids: dict[tuple[Any, Any, Any], Any] = {}
        for hit in result.hits:
            vrow = await self.quality_repo.add_violation(
                row,
                turn_id=hit.turn_id,
                rule_id=hit.rule_id,
                severity=hit.severity,
                redline_tag=hit.redline_tag,
                speaker=hit.speaker,
                evidence_text=hit.evidence_text,
                source=hit.source,
                word=hit.word or None,
                kb_snippet=hit.kb_snippet,
            )
            if vrow is not None and getattr(vrow, "id", None) is not None:
                violation_ids[(hit.rule_id, hit.turn_id, hit.speaker)] = vrow.id
        if result.alerts:
            alert_repo = AlertRepo(self.quality_repo.session)
            for alert in result.alerts:
                vid = violation_ids.get((alert.get("rule_id"), alert.get("turn_id"), alert.get("speaker")))
                inserted = await alert_repo.insert_high(
                    qc_record_id=row.id,
                    violation_id=vid,
                    alert_id=alert.get("id"),
                )
                alert["id"] = str(inserted.id)
                alert["status"] = inserted.status
        await self.quality_repo.record_skipped_rules(
            qc_record_id=row.id,
            skipped_rules=result.skipped_rules,
        )
        commit = getattr(self.quality_repo.session, "commit", None)
        if commit is not None:
            await commit()
        result.qc_record_id = str(row.id)
        result.inserted = True
        if self.job_store is not None and qc_status != "PARTIAL":
            await self.job_store.set_done(
                qc.session_id,
                qc_record_id=row.id,
                skipped_rules=result.skipped_rules,
            )
        started = result.started_monotonic
        latency_ms = int((time.perf_counter() - started) * 1000) if started is not None else 0
        emit_check_completed(
            session_id=qc.session_id,
            call_id=qc.call_id,
            latency_ms=latency_ms,
            score=compliance,
            risk=risk,
            grade=result.grade,
            violation_count=len(result.hits),
        )
        await self._publish_terminal_events(result, qc)
        await safe_append(
            getattr(self.offline, "trace_store", None),
            qc.session_id,
            make_trace_event(
                category="events",
                name="outbound",
                status="ok",
                summary=f"inserted={result.inserted} qc_status={result.qc_status}",
            ),
        )
        return result

    async def _publish_terminal_events(self, result: QcRunResult, qc: QCSession) -> None:
        """落库后按条件 PUBLISH。幂等未重跑不走这里。WAIT_TEXT/未插表不会进入。"""
        if not result.inserted or not result.qc_record_id:
            return
        pub = self.offline.publisher
        if pub is None:
            return
        call_id = event_call_id(qc.call_id, session_id=qc.session_id)
        for alert in result.alerts:
            await pub.publish_alert_raised(
                alert_id=str(alert.get("id") or ""),
                session_id=qc.session_id,
                call_id=call_id,
                level=str(alert.get("level") or "high"),
                evidence=alert.get("evidence"),
            )
        if result.stop_call_eligible:
            evidence = next(
                (
                    hit.evidence_text
                    for hit in result.hits
                    if hit.speaker == "agent" and hit.rule_id == "R13"
                ),
                "",
            )
            await pub.publish_stop_call_detected(
                session_id=qc.session_id,
                case_id=qc.case_id,
                evidence=evidence,
            )
        if result.suspected_eligible:
            tags: list[str] = []
            for hit in result.hits:
                if (
                    hit.speaker == "agent"
                    and hit.severity == "high"
                    and hit.rule_id in SUSPECTED_RULE_IDS
                    and hit.redline_tag
                    and hit.redline_tag not in tags
                ):
                    tags.append(hit.redline_tag)
            await pub.publish_complaint_suspected(
                qc_record_id=result.qc_record_id,
                session_id=qc.session_id,
                case_id=qc.case_id,
                redline_tags=tags,
            )
        mapped = result.to_event()
        await pub.publish_check_completed(
            qc_record_id=result.qc_record_id,
            session_id=qc.session_id,
            call_id=call_id,
            case_id=qc.case_id,
            score=mapped["score"],
            risk=mapped["risk"],
            grade=mapped["grade"],
        )

    async def replay_session(
        self,
        qc: QCSession,
        *,
        force: bool = False,
        persist: bool = True,
    ) -> QcRunResult:
        if self.quality_repo is not None:
            exists = await self.quality_repo.call_record_exists(qc.session_id)
            if not exists:
                return QcRunResult(
                    code=ErrorCode.A2003.value,
                    message=NO_MASTER_MSG,
                    inserted=False,
                    session_id=qc.session_id,
                    case_id=qc.case_id,
                    call_id=qc.call_id,
                )
            await self._assert_force_rerun(qc.session_id, force=force)
        retries = 0
        if self.job_store is not None:
            retries = await self.job_store.get_wait_retries(qc.session_id)
        result = await self.run(qc, call_records_exists=True, text_retry_count=retries)
        result.session_id = qc.session_id
        result.case_id = qc.case_id
        result.call_id = qc.call_id
        if result.job_status == "WAIT_TEXT" and self.job_store is not None:
            await self.job_store.incr_wait_retries(qc.session_id)
        if persist:
            result = await self._persist(result, qc)
        return result

    async def replay_by_session_id(self, session_id: str, *, force: bool = False) -> QcRunResult:
        if self.dp_client is None:
            raise RuntimeError("DpClient is required for path replay")
        t0 = time.perf_counter()
        started = utc_now()
        qc = await self.dp_client.get_qc_source(session_id)
        await safe_append(
            getattr(self.offline, "trace_store", None),
            session_id,
            make_trace_event(
                category="dp",
                name="qc-source",
                status="ok",
                summary=f"turns={len(qc.turns or [])}",
                duration_ms=elapsed_ms(t0),
                started_at=started,
            ),
        )
        await safe_set_meta(
            getattr(self.offline, "trace_store", None),
            session_id,
            call_id=event_call_id(qc.call_id, session_id=session_id),
        )
        return await self.replay_session(qc, force=force, persist=True)

    async def replay_raw(self, qc: QCSession, *, force: bool = False, idempotency_key: str) -> QcRunResult:
        if self.job_store is not None:
            prev = await self.job_store.get_replay_run(qc.session_id)
            if prev == idempotency_key and self.quality_repo is not None:
                existing = await self.quality_repo.get_by_session_id(qc.session_id)
                if existing is not None:
                    return _result_from_record(existing, qc)
            await self.job_store.set_replay_run(qc.session_id, idempotency_key)
        return await self.replay_session(qc, force=force, persist=True)
