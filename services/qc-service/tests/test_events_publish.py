"""QC-7.1：六频道 Redis PUBLISH payload 与触发点。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.events.publisher import (
    ALERT_STREAM,
    CHANNEL_ALERT_RAISED,
    CHANNEL_CHECK_COMPLETED,
    CHANNEL_COMPLAINT_SUSPECTED,
    CHANNEL_REVIEW_DONE,
    CHANNEL_STOP_CALL,
    CHANNEL_SYSTEM_ALERT,
    FORBIDDEN_CHANNELS,
    QcEventPublisher,
    sample_type_for,
)
from app.integrations.qc_session import QCSession
from app.repositories.job_store import QcJobStore
from app.services.ac_filter import WordHit
from app.services.alert_service import AlertService
from app.services.offline_qc_service import OfflineQcService, QcRunResult, stop_call_eligible, suspected_eligible
from app.services.replay_service import ReplayService, _result_from_record
from app.services.review_service import ReviewService
from tests.test_job_store import MemoryRedis
from tests.test_replay_api import ApiFakeSession, FakeQualityRepo, _ready

CALL_UUID = "22222222-3333-4444-5555-666666666666"


def _hit(**kwargs) -> WordHit:
    return WordHit(
        speaker=kwargs.get("speaker", "agent"),
        word=kwargs.get("word", "x"),
        severity=kwargs.get("severity", "high"),
        score_eligible=kwargs.get("score_eligible", True),
        source=kwargs.get("source", "rule"),
        rule_id=kwargs.get("rule_id"),
        redline_tag=kwargs.get("redline_tag"),
        evidence_text=kwargs.get("evidence_text", "证据"),
    )


def _svc(redis: MemoryRedis | None = None):
    redis = redis or MemoryRedis(db=3)
    pub = QcEventPublisher(redis)
    db = ApiFakeSession()
    db.call_ids.add("sess_ok")
    store = QcJobStore(redis)
    repo = FakeQualityRepo(db, store)
    offline = OfflineQcService(publisher=pub, job_store=store)
    replay = ReplayService(offline, job_store=store, quality_repo=repo)
    return replay, pub, redis, db


async def _persist(replay: ReplayService, result: QcRunResult, **qc_kw) -> QcRunResult:
    payload = _ready()
    payload.update(qc_kw)
    qc = QCSession.model_validate(payload)
    result.session_id = qc.session_id
    result.case_id = qc.case_id
    result.call_id = qc.call_id
    return await replay._persist(result, qc)


class TestCompletedPayload:
    async def test_completed_uses_score_risk_and_null_call_id(self):
        replay, pub, redis, _db = _svc()
        result = QcRunResult(
            code="0",
            compliance_score=80,
            risk_level="high",
            grade="PASS",
            qc_result="pending_review",
            qc_status="PENDING_REVIEW",
            hits=[],
            inserted=False,
        )
        await _persist(replay, result, call_id=None)
        completed = [p for ch, p in pub.emitted if ch == CHANNEL_CHECK_COMPLETED]
        assert completed
        payload = completed[0]
        assert payload["score"] == 80
        assert payload["risk"] == "high"
        assert payload["grade"] == "PASS"
        assert payload["call_id"] is None
        assert payload["call_id"] != payload["session_id"]
        assert payload["session_id"] == "sess_ok"
        assert "qc_result" not in payload
        assert "qc_status" not in payload
        assert "skipped_rules" not in payload
        assert "compliance_score" not in payload
        assert "risk_level" not in payload
        assert "ts" in payload and "trace_id" in payload
        raw = json.loads(redis.published[-1][1])
        assert raw["score"] == 80

    async def test_completed_call_id_is_uuid_not_session(self):
        replay, pub, _redis, _db = _svc()
        result = QcRunResult(code="0", compliance_score=90, risk_level="low", grade="EXCELLENT")
        await _persist(replay, result, call_id=CALL_UUID, session_id="sess_ok")
        payload = [p for ch, p in pub.emitted if ch == CHANNEL_CHECK_COMPLETED][0]
        assert payload["call_id"] == CALL_UUID
        assert payload["call_id"] != "sess_ok"


class TestAlertAndSuspected:
    async def test_r05_only_no_alert_no_suspected(self):
        replay, pub, redis, _db = _svc()
        hits = [_hit(rule_id="R05", severity="low", redline_tag="threat", score_eligible=True)]
        alerts = AlertService().on_high_risk(hits)
        result = QcRunResult(
            code="0",
            compliance_score=98,
            risk_level="low",
            grade="EXCELLENT",
            hits=hits,
            alerts=alerts,
            suspected_eligible=suspected_eligible(hits),
        )
        await _persist(replay, result)
        channels = [ch for ch, _ in pub.emitted]
        assert CHANNEL_COMPLAINT_SUSPECTED not in channels
        assert CHANNEL_ALERT_RAISED not in channels
        assert CHANNEL_CHECK_COMPLETED in channels
        assert "qc.escalate.human" not in channels
        assert not redis.xadd_log

    async def test_agent_r12_high_publishes_suspected_with_qc_record_id(self):
        replay, pub, _redis, _db = _svc()
        hits = [
            _hit(
                rule_id="R12",
                severity="high",
                redline_tag="fake_identity",
                evidence_text="我是法院的",
            )
        ]
        alerts = AlertService().on_high_risk(hits)
        result = QcRunResult(
            code="0",
            compliance_score=80,
            risk_level="high",
            grade="PASS",
            hits=hits,
            alerts=alerts,
            suspected_eligible=True,
        )
        out = await _persist(replay, result)
        suspected = [p for ch, p in pub.emitted if ch == CHANNEL_COMPLAINT_SUSPECTED]
        assert suspected
        assert suspected[0]["qc_record_id"] == out.qc_record_id
        assert suspected[0]["qc_record_id"]
        assert CHANNEL_ALERT_RAISED in [ch for ch, _ in pub.emitted]


class TestForbiddenChannels:
    async def test_high_risk_never_publishes_escalate_or_llm_channel(self):
        replay, pub, redis, _db = _svc()
        hits = [_hit(rule_id="R12", severity="high", redline_tag="fake_identity")]
        result = QcRunResult(
            code="0",
            compliance_score=80,
            risk_level="high",
            grade="PASS",
            hits=hits,
            alerts=AlertService().on_high_risk(hits),
            suspected_eligible=True,
        )
        await _persist(replay, result)
        channels = {ch for ch, _ in pub.emitted}
        assert "qc.escalate.human" not in channels
        assert "qc.llm.degraded" not in channels
        assert FORBIDDEN_CHANNELS.isdisjoint(channels)
        pub_channels = {ch for ch, _ in redis.published}
        assert "qc.escalate.human" not in pub_channels
        assert "qc.llm.degraded" not in pub_channels


class TestWaitText:
    async def test_wait_text_does_not_publish_completed(self):
        replay, pub, redis, db = _svc()
        result = QcRunResult(code="A2003", job_status="WAIT_TEXT", inserted=False)
        await _persist(replay, result)
        assert CHANNEL_CHECK_COMPLETED not in [ch for ch, _ in pub.emitted]
        from app.models.qc import QcRecord

        assert not any(isinstance(obj, QcRecord) for obj in db.added)
        assert redis.published == []


class TestReviewDone:
    async def test_three_sample_types(self):
        pub = QcEventPublisher()
        review = ReviewService(pub)
        rid = str(uuid4())
        mapping = {"CONFIRM": "positive", "DISMISS": "negative", "CORRECT": "corrected"}
        for action, expected in mapping.items():
            assert sample_type_for(action) == expected
            assert review.sample_type(action) == expected
            payload = await review.publish_done(qc_record_id=rid, action=action, revised_score=80)
            assert payload["sample_type"] == expected
            assert payload["action"] == action
            assert payload["qc_record_id"] == rid
        channels = [ch for ch, _ in pub.emitted]
        assert channels.count(CHANNEL_REVIEW_DONE) == 3
        with pytest.raises(ValueError):
            sample_type_for("OTHER")


class TestAlertStream:
    async def test_alert_raised_xadd_and_masks_phone(self):
        redis = MemoryRedis(db=3)
        replay, pub, redis, _db = _svc(redis)
        phone = "13812345678"
        hits = [
            _hit(
                rule_id="R12",
                severity="high",
                redline_tag="fake_identity",
                evidence_text=f"请回拨{phone}",
            )
        ]
        result = QcRunResult(
            code="0",
            compliance_score=80,
            risk_level="high",
            grade="PASS",
            hits=hits,
            alerts=AlertService().on_high_risk(hits),
            suspected_eligible=True,
        )
        await _persist(replay, result)
        alerts = [p for ch, p in pub.emitted if ch == CHANNEL_ALERT_RAISED]
        assert alerts
        assert phone not in alerts[0]["evidence"]
        assert redis.xadd_log
        stream, fields = redis.xadd_log[0]
        assert stream == ALERT_STREAM
        evidence = json.loads(fields["evidence"])
        assert phone not in evidence
        joined = json.dumps(redis.published)
        assert phone not in joined


class TestStopCall:
    async def test_user_only_r13_does_not_publish_stop_call(self):
        replay, pub, _redis, _db = _svc()
        hits = [_hit(speaker="user", rule_id="R13", severity="high", score_eligible=False)]
        result = QcRunResult(
            code="0",
            compliance_score=100,
            risk_level="low",
            grade="EXCELLENT",
            hits=hits,
            stop_call_eligible=stop_call_eligible(hits),
        )
        await _persist(replay, result)
        assert CHANNEL_STOP_CALL not in [ch for ch, _ in pub.emitted]

    async def test_agent_r13_publishes_stop_call(self):
        replay, pub, _redis, _db = _svc()
        hits = [
            _hit(
                speaker="agent",
                rule_id="R13",
                severity="high",
                score_eligible=True,
                redline_tag="stop_call",
                evidence_text="那还是要还的",
            )
        ]
        result = QcRunResult(
            code="0",
            compliance_score=80,
            risk_level="high",
            grade="PASS",
            hits=hits,
            alerts=AlertService().on_high_risk(hits),
            suspected_eligible=True,
            stop_call_eligible=True,
        )
        await _persist(replay, result)
        stop = [p for ch, p in pub.emitted if ch == CHANNEL_STOP_CALL]
        assert stop
        assert stop[0]["session_id"] == "sess_ok"
        assert "case_id" in stop[0]


class TestIdempotentNoRepeat:
    async def test_result_from_record_does_not_publish(self):
        from app.models.qc import QcRecord

        replay, pub, redis, db = _svc()
        row = QcRecord(
            session_id="sess_ok",
            compliance_score=80,
            risk_level="high",
            qc_result="pending_review",
            qc_status="DONE",
        )
        row.id = uuid4()
        db.qc_by_session["sess_ok"] = row
        await redis.set("qc:replay:run:sess_ok", "idem-1")
        qc = QCSession.model_validate(_ready())
        out = await replay.replay_raw(qc, idempotency_key="idem-1")
        assert out.qc_record_id == str(row.id)
        assert pub.emitted == []
        assert _result_from_record(row, qc).inserted is False


class TestSystemAlertUnchanged:
    async def test_system_alert_not_llm_degraded_channel(self):
        pub = QcEventPublisher()
        await pub.publish_system_alert_raised()
        assert pub.emitted[0][0] == CHANNEL_SYSTEM_ALERT
        assert pub.emitted[0][1]["event"] == "qc:llm_degraded"
        assert "qc.llm.degraded" in FORBIDDEN_CHANNELS
        with pytest.raises(ValueError):
            await pub._publish("qc.escalate.human", {"x": 1})
        with pytest.raises(ValueError):
            await pub._publish("qc.llm.degraded", {"x": 1})
