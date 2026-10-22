"""QC-10.2：埋点事件名（日志），无 realtime / escalate。"""
from __future__ import annotations

import inspect
import time

from app.events.publisher import FORBIDDEN_CHANNELS, QcEventPublisher
from app.integrations.qc_session import QCSession
from app.observability import (
    captured_events,
    emit_alert_raised,
    emit_check_started,
    evaluate_words_stale,
    reset_events,
)
from app.repositories.job_store import QcJobStore
from app.services.alert_service import AlertService
from app.services.llm_reviewer import LLMReviewer
from app.services.offline_qc_service import OfflineQcService
from app.services.replay_service import ReplayService
from tests.test_job_store import MemoryRedis
from tests.test_offline_qc import _empty_llm, _ready_session
from tests.test_replay_api import ApiFakeSession, FakeQualityRepo, _ready


class TestObservabilityEvents:
    def setup_method(self):
        reset_events()

    def test_started_mode_is_offline(self):
        emit_check_started(session_id="sess_1", call_id=None)
        started = [e for e in captured_events() if e["event"] == "qc.check.started"]
        assert started
        assert started[0]["mode"] == "offline"
        assert started[0]["call_id"] is None
        assert started[0]["call_id"] != "sess_1"

    def test_no_escalate_triggered_and_no_realtime(self):
        emit_check_started(session_id="sess_1", call_id=None)
        names = {e["event"] for e in captured_events()}
        assert "qc.escalate.triggered" not in names
        assert all(e.get("mode") != "realtime" for e in captured_events())

    async def test_wait_text_emits_wait_text_event(self):
        svc = OfflineQcService(llm=LLMReviewer(invoke=_empty_llm, retry_delay=0.0))
        await svc.run({"session_id": "sess_wait", "call_ended": False, "turns": []})
        names = [e["event"] for e in captured_events()]
        assert "qc.check.wait_text" in names
        assert "qc.escalate.triggered" not in names
        assert all(e.get("mode") != "realtime" for e in captured_events())

    async def test_pipeline_started_offline_on_ready_session(self):
        svc = OfflineQcService(llm=LLMReviewer(invoke=_empty_llm, retry_delay=0.0))
        await svc.run(_ready_session())
        started = [e for e in captured_events() if e["event"] == "qc.check.started"]
        assert started
        assert started[0]["mode"] == "offline"
        llm_started = [e for e in captured_events() if e["event"] == "llm.review.started"]
        assert llm_started
        assert all(e.get("mode") != "realtime" for e in captured_events())
        assert not any(e["event"] == "qc.escalate.triggered" for e in captured_events())

    async def test_persist_emits_completed_score_and_risk(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        repo = FakeQualityRepo(db, store)
        offline = OfflineQcService(
            llm=LLMReviewer(invoke=_empty_llm, retry_delay=0.0),
            job_store=store,
        )
        replay = ReplayService(offline, job_store=store, quality_repo=repo)
        qc = QCSession.model_validate(_ready())
        await replay.replay_session(qc, persist=True)
        completed = [e for e in captured_events() if e["event"] == "qc.check.completed"]
        assert completed
        assert completed[0]["score"] == 20
        assert completed[0]["risk"] == "high"
        assert completed[0]["call_id"] != qc.session_id
        assert completed[0].get("mode") != "realtime"
        assert not any(e["event"] == "qc.escalate.triggered" for e in captured_events())

    def test_publisher_allows_six_forbids_escalate(self):
        assert "qc.escalate.human" in FORBIDDEN_CHANNELS
        assert "qc.llm.degraded" in FORBIDDEN_CHANNELS
        assert "qc.check.completed" not in FORBIDDEN_CHANNELS
        assert "qc.alert.raised" not in FORBIDDEN_CHANNELS
        assert hasattr(QcEventPublisher, "publish_check_completed")
        src = inspect.getsource(ReplayService._persist)
        assert "qc.escalate.human" not in src

    def test_alert_log_does_not_publish_channel(self):
        from app.services.ac_filter import WordHit

        alerter = AlertService()
        hits = [
            WordHit(
                speaker="agent",
                word="R12",
                severity="high",
                score_eligible=True,
                source="rule",
                rule_id="R12",
                redline_tag="fake_identity",
                evidence_text="我是法院的",
            )
        ]
        written = alerter.on_high_risk(hits)
        assert written
        names = {e["event"] for e in captured_events()}
        assert "qc.alert.raised" in names
        pub = QcEventPublisher()
        channels = {ch for ch, _ in pub.emitted}
        assert "qc.alert.raised" not in channels

    def test_alert_level_not_passed_as_structlog_level(self, monkeypatch):
        seen: dict = {}

        def _info(event, **kwargs):
            seen["event"] = event
            seen["kwargs"] = kwargs

        monkeypatch.setattr("app.observability.logger.info", _info)
        emit_alert_raised(call_id=None, level="high")
        stored = [e for e in captured_events() if e["event"] == "qc.alert.raised"]
        assert stored[0]["level"] == "high"
        assert seen["kwargs"].get("level") != "high"
        assert seen["kwargs"].get("alert_level") == "high"

    def test_words_stale_after_two_hours(self):
        reset_events()
        t0 = time.time()
        stale = evaluate_words_stale(
            now=t0 + 2 * 3600 + 1,
            last_load=t0,
            fetch_failing=False,
            qc_version="3",
            rag_version="3",
        )
        assert stale is True
        assert any(e["event"] == "rag.words.stale" for e in captured_events())
