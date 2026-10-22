"""QC-7.1：六频道 Redis PUBLISH payload 与触发点。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.events.publisher import (
    ALERT_STREAM,
    ALLOWED_CHANNELS,
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
    async def test_completed_uses_score_risk_and_null_call_id(self, _mock_event_bus):
        replay, pub, _redis, _db = _svc()
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
        assert payload["deduction"] == 20
        assert payload["score"] == max(0, 100 - payload["deduction"])
        assert payload["risk"] == "high"
        assert payload["grade"] is None
        assert payload["call_id"] is None
        assert payload["call_id"] != payload["session_id"]
        assert payload["session_id"] == "sess_ok"
        assert "qc_result" not in payload
        assert "qc_status" not in payload
        assert "skipped_rules" not in payload
        assert "compliance_score" not in payload
        assert "deduction_total" not in payload
        assert "risk_level" not in payload
        assert "ts" in payload and "trace_id" in payload
        # Wire format goes through EventBus (mocked); envelope structure is
        # covered by common/eventbus tests. Here assert whitelist data only.
        bus = _mock_event_bus
        calls = [
            c for c in bus.publish.await_args_list
            if c.args[0] == CHANNEL_CHECK_COMPLETED
        ]
        assert calls
        data = calls[0].args[1]
        assert data.get("session_id") == "sess_ok"
        assert "score" not in data
        assert "deduction" not in data
        assert payload["score"] == 80

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
            rev_ded = 0 if action == "DISMISS" else (20 if action == "CORRECT" else None)
            rev_score = None if action == "DISMISS" else (80 if action == "CORRECT" else None)
            payload = await review.publish_done(
                qc_record_id=rid, action=action,
                revised_deduction=rev_ded, revised_score=rev_score,
            )
            assert payload["sample_type"] == expected
            assert payload["action"] == action
            assert payload["qc_record_id"] == rid
            assert payload["revised_deduction"] == rev_ded
            assert payload["revised_score"] == rev_score
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
        assert pub.emitted[0][1]["error_code"] == "A2011"
        assert "qc.llm.degraded" in FORBIDDEN_CHANNELS
        with pytest.raises(ValueError):
            await pub._publish("qc.escalate.human", {"x": 1})
        with pytest.raises(ValueError):
            await pub._publish("qc.llm.degraded", {"x": 1})


class TestResilienceHooks:
    def teardown_method(self):
        from app.events.resilience import clear_emitted
        from app.main import _unwire_resilience_hooks
        from common.resilience._redis import reset_redis

        from tests.test_replay_api import _cleanup

        _cleanup()
        _unwire_resilience_hooks()
        clear_emitted()
        reset_redis()

    def test_allowed_channels_still_exactly_six(self):
        assert len(ALLOWED_CHANNELS) == 6
        assert ALLOWED_CHANNELS == {
            CHANNEL_ALERT_RAISED,
            CHANNEL_CHECK_COMPLETED,
            CHANNEL_COMPLAINT_SUSPECTED,
            CHANNEL_STOP_CALL,
            CHANNEL_REVIEW_DONE,
            CHANNEL_SYSTEM_ALERT,
        }

    def test_rate_limit_hit_emits_qc_event_with_trace_id_not_redis_channel(self):
        from app.events.resilience import clear_emitted, emitted
        from app.main import wire_resilience_hooks
        from tests.test_replay_api import (
            ApiFakeSession,
            FakeDp,
            _assert_envelope,
            _client,
            _ready,
        )

        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        wire_resilience_hooks()
        clear_emitted()
        client = _client(db, redis, FakeDp({}))
        for i in range(11):
            resp = client.post(
                "/api/v1/qc/replay/raw",
                headers={
                    "X-Idempotency-Key": f"hook-{i}",
                    "X-User-Id": "u-hook-rate",
                    "X-Trace-Id": "trace-hook-rate",
                },
                json=_ready(),
            )
        _assert_envelope(resp.json(), code="B0006")
        hits = [payload for name, payload in emitted if name == "qc.rate_limit.hit"]
        assert hits
        assert hits[0]["trace_id"] == "trace-hook-rate"
        assert hits[0]["key"] == "qc:ratelimit:raw:u-hook-rate"
        assert hits[0]["reason"] == "rate_limited"
        published = {ch for ch, _ in redis.published}
        assert "qc.rate_limit.hit" not in published
        assert "qc.llm.degraded" not in published
        assert published <= ALLOWED_CHANNELS

    def test_shutdown_sets_hooks_publisher_none(self):
        from app.main import _unwire_resilience_hooks, app, wire_resilience_hooks
        from common.resilience import hooks
        from fastapi.testclient import TestClient

        wire_resilience_hooks()
        assert hooks.get_publisher() is not None
        _unwire_resilience_hooks()
        assert hooks.get_publisher() is None

        with TestClient(app):
            assert hooks.get_publisher() is not None
        assert hooks.get_publisher() is None

    async def test_adapter_failure_does_not_block_rate_limit(self):
        from common.resilience import hooks
        from tests.test_replay_api import (
            ApiFakeSession,
            FakeDp,
            _assert_envelope,
            _client,
            _ready,
        )

        async def _boom(_name, _payload):
            raise RuntimeError("adapter down")

        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        hooks.set_publisher(_boom)
        client = _client(db, redis, FakeDp({}))
        codes = []
        for i in range(11):
            resp = client.post(
                "/api/v1/qc/replay/raw",
                headers={"X-Idempotency-Key": f"boom-{i}", "X-User-Id": "u-boom"},
                json=_ready(),
            )
            codes.append((resp.status_code, resp.json()["code"]))
        assert codes[:10] == [(200, "0")] * 10
        _assert_envelope(resp.json(), code="B0006")
        assert codes[10] == (429, "B0006")

    async def test_adapter_records_circuit_and_degrade_variants_not_redis(self):
        from app.events.resilience import clear_emitted, emitted, on_resilience_event
        from common.resilience import hooks

        clear_emitted()
        hooks.set_publisher(on_resilience_event)
        await hooks.on_event(
            "qc.circuit_breaker.open",
            {"key": "qc:llm_degraded:qwen-plus", "reason": "open"},
        )
        await hooks.on_event(
            "qc.degrade.triggered",
            {"key": "qc:llm_degraded:qwen-plus", "reason": "circuit_open"},
        )
        await hooks.on_event(
            "qc.rate_limit.degraded",
            {"key": "qc:ratelimit:raw:anon", "error": "redis down"},
        )
        names = [name for name, _ in emitted]
        assert "qc.circuit_breaker.open" in names
        assert "qc.degrade.triggered" in names
        assert "qc.rate_limit.degraded" in names
        assert all(not name.startswith("qc.llm.degraded") for name, _ in emitted)

    async def test_adapter_swallows_internal_errors(self, monkeypatch):
        from app.events import resilience

        class _Boom:
            def append(self, _item):
                raise RuntimeError("emitted list down")

            def clear(self):  # teardown _cleanup() calls clear_emitted()
                pass

        monkeypatch.setattr(resilience, "emitted", _Boom())
        await resilience.on_resilience_event(
            "qc.circuit_breaker.open",
            {"key": "qc:llm_degraded:qwen-plus"},
        )
