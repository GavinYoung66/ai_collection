"""QC-11.2 轮次 3.2：DD-02 §8.2 断言 1–14 可执行验收。MOCK_LLM=1。不含 15–28。"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.deps import get_alert_repo, get_redis, get_review_repo
from app.events.publisher import CHANNEL_ALERT_RAISED, CHANNEL_STOP_CALL, FORBIDDEN_CHANNELS
from app.integrations.qc_session import QCSession
from app.main import app
from app.models.qc import QcAlert, QcRecord, QcReview, QcViolation
from app.repositories.alert_repo import AlertRepo
from app.repositories.job_store import QcJobStore
from app.routers.check import get_dp_client, get_llm_reviewer, get_quality_repo
from app.services.ac_filter import WordHit
from app.services.dedup_ac import DedupACWindow
from app.services.llm_reviewer import LLMReviewer
from app.services.scoring_service import ScoringService
from common.db.session import get_session
from common.security.mask import mask_text
from common.security.rbac import create_token
from tests.test_job_store import MemoryRedis
from tests.test_replay_api import (
    ENVELOPE_KEYS,
    ApiFakeSession,
    FakeDp,
    FakeQualityRepo,
    _empty_llm,
    _qc_records,
    _ready,
)

PHONE = "13812345678"
ID_CARD = "110101199001011234"
FIXTURE_QC_SOURCE = Path(__file__).resolve().parent / "fixtures" / "qc_source.json"


@pytest.fixture(autouse=True)
def _mock_llm_env(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("QC_MOCK_LLM", "true")
    monkeypatch.setattr("app.core.config.qc_settings.QC_MOCK_LLM", True)
    monkeypatch.setattr("app.services.llm_reviewer.qc_settings.QC_MOCK_LLM", True)


def _token(role: str, *, sub: str | None = None) -> dict[str, str]:
    uid = sub or str(uuid4())
    return {"Authorization": f"Bearer {create_token({'sub': uid, 'role': role})}"}


def _turn(speaker: str, text: str, index: int, **extra) -> dict:
    row = {
        "turn_id": f"t{index}",
        "speaker": speaker,
        "text": text,
        "turn_index": index,
    }
    row.update(extra)
    return row


def _serious_turns() -> list[dict]:
    """至少 3 条可计分 agent-high：R01 + R12 + R13。禁止单条 −20。"""
    return [
        _turn("agent", "让你妈妈接电话", 1),
        _turn("agent", "我是法院的，过来配合调查", 2),
        _turn("user", "你们不要再打了", 3),
        _turn("agent", "赶紧还钱", 4),
    ]


def _one_high_turns() -> list[dict]:
    return [_turn("agent", "让你妈妈接电话", 1)]


class MaskingQualityRepo(FakeQualityRepo):
    async def add_violation(self, record, **fields):
        evidence = fields.get("evidence_text")
        if evidence:
            fields["evidence_text"] = mask_text(str(evidence))
        row = QcViolation(
            qc_record_id=record.id,
            turn_id=fields.get("turn_id"),
            rule_id=fields.get("rule_id"),
            severity=fields.get("severity") or "low",
            redline_tag=fields.get("redline_tag"),
            speaker=fields.get("speaker") or "agent",
            evidence_text=fields.get("evidence_text"),
            source=fields.get("source"),
            word=fields.get("word"),
            kb_snippet=fields.get("kb_snippet"),
        )
        row.id = uuid4()
        self.session.add(row)
        return row


class SessionAlertRepo:
    def __init__(self, session: ApiFakeSession):
        self.session = session

    async def list_alerts(self, *, page, page_size, status=None, session_id=None):
        records = {str(row.id): row for row in self.session.qc_by_session.values()}
        evidence_by_vid = {
            str(obj.id): obj.evidence_text
            for obj in self.session.added
            if isinstance(obj, QcViolation)
        }
        items = []
        for obj in self.session.added:
            if not isinstance(obj, QcAlert):
                continue
            rec = records.get(str(obj.qc_record_id))
            sid = rec.session_id if rec is not None else None
            if status and obj.status != status:
                continue
            if session_id and sid != session_id:
                continue
            evidence = evidence_by_vid.get(str(obj.violation_id or ""))
            items.append(AlertRepo._item(obj, session_id=sid, evidence=evidence))
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total


class SessionReviewRepo:
    def __init__(self, session: ApiFakeSession):
        self.session = session
        self.reviews: list[QcReview] = []

    async def get_record(self, qc_record_id):
        key = str(qc_record_id)
        for row in self.session.qc_by_session.values():
            if str(row.id) == key:
                return row
        return None

    async def insert_review(self, **kwargs) -> QcReview:
        row = QcReview(
            qc_record_id=kwargs["qc_record_id"],
            action=kwargs["action"],
            revised_score=kwargs.get("revised_score"),
            reason=kwargs.get("reason"),
            reviewer_id=kwargs.get("reviewer_id"),
        )
        row.id = uuid4()
        self.reviews.append(row)
        self.session.add(row)
        return row


def _client(
    session: ApiFakeSession,
    redis: MemoryRedis,
    dp: FakeDp,
    *,
    llm=None,
) -> TestClient:
    async def _session():
        yield session

    async def _redis():
        return redis

    def _llm():
        return llm or LLMReviewer(invoke=_empty_llm)

    store = QcJobStore(redis)
    quality = MaskingQualityRepo(session, store)
    alerts = SessionAlertRepo(session)
    reviews = SessionReviewRepo(session)

    async def _quality():
        return quality

    async def _alerts():
        return alerts

    async def _reviews():
        return reviews

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    app.dependency_overrides[get_dp_client] = lambda: dp
    app.dependency_overrides[get_llm_reviewer] = _llm
    app.dependency_overrides[get_quality_repo] = _quality
    app.dependency_overrides[get_alert_repo] = _alerts
    app.dependency_overrides[get_review_repo] = _reviews
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.clear()


def _assert_envelope(body: dict, *, code: str | None = None) -> None:
    assert ENVELOPE_KEYS <= set(body)
    if code is not None:
        assert body["code"] == code


def _published_channels(redis: MemoryRedis) -> list[str]:
    return [channel for channel, _msg in redis.published]


def _payloads(redis: MemoryRedis, channel: str) -> list[dict]:
    out = []
    for name, raw in redis.published:
        if name != channel:
            continue
        out.append(json.loads(raw) if isinstance(raw, str) else raw)
    return out


def _post_raw(client: TestClient, payload: dict, *, key: str | None = None):
    return client.post(
        "/api/v1/qc/replay/raw",
        headers={"X-Idempotency-Key": key or str(uuid4())},
        json=payload,
    )


def _raw_body(session_id: str, turns: list[dict], **extra) -> dict:
    return _ready(session_id=session_id, case_id=f"case_{session_id}", turns=turns, **extra)


class TestAssertions1to14:
    def teardown_method(self):
        _cleanup()

    def test_assertion_01_raw_serious(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a1")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = _post_raw(client, _raw_body("sess_a1", _serious_turns()))
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        assert "score" not in data
        assert data["compliance_score"] <= 60
        assert data["compliance_score"] != 80
        assert data["risk"] == "high"
        assert data["grade"] == "SERIOUS"
        agent_high = [
            hit
            for hit in data["hits"]
            if hit.get("speaker") == "agent" and hit.get("severity") == "high"
        ]
        assert len(agent_high) >= 3

    def test_assertion_02_high_count_one(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a2")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = _post_raw(client, _raw_body("sess_a2", _one_high_turns()))
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        assert data["compliance_score"] == 80
        assert data["risk"] == "high"
        assert data["grade"] == "PASS"
        rows = _qc_records(db)
        assert len(rows) == 1
        assert rows[0].high_count == 1

    def test_assertion_03_alerts_one_new_high(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a3")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        replay = _post_raw(client, _raw_body("sess_a3", _one_high_turns()))
        _assert_envelope(replay.json(), code="0")
        resp = client.get(
            "/api/v1/qc/alerts",
            headers=_token("SUPV"),
            params={"session_id": "sess_a3", "status": "NEW"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        items = body["data"]["items"]
        assert len(items) == 1
        assert items[0]["status"] == "NEW"
        assert items[0]["level"] == "high"
        assert items[0]["session_id"] == "sess_a3"
        assert items[0]["qc_record_id"]

    def test_assertion_04_alert_raised_has_alert_id(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a4")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        replay = _post_raw(client, _raw_body("sess_a4", _one_high_turns()))
        _assert_envelope(replay.json(), code="0")
        raised = _payloads(redis, CHANNEL_ALERT_RAISED)
        assert raised
        assert raised[0].get("alert_id")
        assert raised[0]["session_id"] == "sess_a4"
        assert CHANNEL_ALERT_RAISED not in FORBIDDEN_CHANNELS

    def test_assertion_05_no_escalate_human(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a5")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        replay = _post_raw(client, _raw_body("sess_a5", _serious_turns()))
        _assert_envelope(replay.json(), code="0")
        channels = set(_published_channels(redis))
        assert "qc.escalate.human" not in channels
        assert FORBIDDEN_CHANNELS.isdisjoint(channels)
        assert "qc.escalate.human" in FORBIDDEN_CHANNELS

    def test_assertion_06_same_word_20s_score_98(self):
        hits = [
            WordHit(
                speaker="agent",
                word="可以减免",
                severity="low",
                score_eligible=True,
                source="ac",
                word_id="w_induce",
                normalized_word="可以减免",
                turn_id="t1",
                turn_index=1,
                hit_ts="2026-09-09T10:00:00+08:00",
            ),
            WordHit(
                speaker="agent",
                word="可以减免",
                severity="low",
                score_eligible=True,
                source="ac",
                word_id="w_induce",
                normalized_word="可以减免",
                turn_id="t2",
                turn_index=2,
                hit_ts="2026-09-09T10:00:20+08:00",
            ),
        ]
        kept = DedupACWindow(30000).apply(hits)
        scored = ScoringService().score(kept)
        assert len([hit for hit in kept if hit.score_eligible]) == 1
        assert scored.compliance_score == 98

    def test_assertion_07_r02_2330_minus_8(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a7")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _raw_body(
            "sess_a7",
            [_turn("agent", "您好请问现在方便吗", 1)],
            started_at="2026-09-09T23:30:00+08:00",
        )
        resp = _post_raw(client, payload)
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        r02 = [hit for hit in data["hits"] if hit.get("rule_id") == "R02"]
        assert r02
        assert data["compliance_score"] == 92
        assert data["qc_result"] == "pending_review"
        assert data["qc_status"] == "PENDING_REVIEW"

    def test_assertion_08_stop_call_r13_user_not_scored(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a8")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _raw_body(
            "sess_a8",
            [
                _turn("user", "不要再打了", 1),
                _turn("agent", "赶紧还钱", 2),
            ],
        )
        resp = _post_raw(client, payload)
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        r13 = [hit for hit in data["hits"] if hit.get("rule_id") == "R13"]
        assert r13
        agent_r13 = [hit for hit in r13 if hit.get("speaker") == "agent"]
        assert agent_r13
        assert data["compliance_score"] == 80
        assert CHANNEL_STOP_CALL in _published_channels(redis)
        user_r13 = [hit for hit in r13 if hit.get("speaker") == "user"]
        assert user_r13

    def test_assertion_09_fake_identity_r12_high(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a9")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = _post_raw(
            client,
            _raw_body("sess_a9", [_turn("agent", "我是法院的，过来配合调查", 1)]),
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        r12 = [hit for hit in body["data"]["hits"] if hit.get("rule_id") == "R12"]
        assert r12
        assert r12[0]["redline_tag"] == "fake_identity"
        assert r12[0]["severity"] == "high"

    def test_assertion_10_review_correct_sets_reviewed(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a10")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        replay = _post_raw(client, _raw_body("sess_a10", _one_high_turns()))
        replay_body = replay.json()
        _assert_envelope(replay_body, code="0")
        qc_record_id = replay_body["data"]["qc_record_id"]
        assert replay_body["data"]["qc_status"] == "PENDING_REVIEW"
        original = replay_body["data"]["compliance_score"]
        resp = client.post(
            f"/api/v1/qc/reviews/{qc_record_id}",
            headers=_token("QC"),
            json={"action": "CORRECT", "revised_score": 72, "reason": "改分"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        assert data["qc_status"] == "REVIEWED"
        assert data["compliance_score"] == 72
        assert data["compliance_score"] != original
        assert "score" not in data
        row = db.qc_by_session["sess_a10"]
        assert row.qc_status == "REVIEWED"
        assert row.compliance_score == 72

    def test_assertion_11_replay_session_from_dp_seed(self):
        raw = json.loads(FIXTURE_QC_SOURCE.read_text(encoding="utf-8"))
        qc = QCSession.model_validate(raw)
        db = ApiFakeSession()
        db.call_ids.add(qc.session_id)
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({qc.session_id: qc}))
        resp = client.post(f"/api/v1/qc/replay/{qc.session_id}", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] in {"0", "A2011"}
        assert ENVELOPE_KEYS <= set(body)
        data = body["data"]
        assert data is not None
        assert data["compliance_score"] is not None
        assert "score" not in data
        assert data["session_id"] == qc.session_id

    def test_assertion_12_llm_circuit_open_still_scores(self, monkeypatch):
        monkeypatch.setattr("app.core.config.qc_settings.QC_MOCK_LLM", False)
        monkeypatch.setattr("app.services.llm_reviewer.qc_settings.QC_MOCK_LLM", False)
        from common.llm import reliability as reliability_mod
        from common.llm.reliability import set_default_service
        from common.resilience._redis import configure_redis

        reliability_mod._llm_breakers.clear()
        set_default_service("qc")
        monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

        async def _boom(model, messages, timeout_seconds):
            raise TimeoutError("timeout")

        db = ApiFakeSession()
        db.call_ids.add("sess_a12")
        redis = MemoryRedis(db=3)
        configure_redis(redis)
        store = QcJobStore(redis)
        llm = LLMReviewer(invoke=_boom, job_store=store, retry_delay=0.0)
        client = _client(db, redis, FakeDp({}), llm=llm)
        resp = _post_raw(client, _raw_body("sess_a12", _one_high_turns()))
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2011")
        data = body["data"]
        assert data is not None
        assert data["compliance_score"] == 80
        assert "score" not in data
        assert data.get("qc_status") != "PARTIAL"

    def test_assertion_13_evidence_masked_no_full_pii(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a13")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        text = f"让你妈妈接电话，回拨{PHONE}，身份证{ID_CARD}"
        resp = _post_raw(client, _raw_body("sess_a13", [_turn("agent", text, 1)]))
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        blob = json.dumps(body["data"], ensure_ascii=False)
        assert PHONE not in blob
        assert ID_CARD not in blob
        for hit in body["data"]["hits"]:
            evidence = hit.get("evidence_text") or ""
            assert PHONE not in evidence
            assert ID_CARD not in evidence
        stored = [obj.evidence_text or "" for obj in db.added if isinstance(obj, QcViolation)]
        assert stored
        for evidence in stored:
            assert PHONE not in evidence
            assert ID_CARD not in evidence

    def test_assertion_14_call_ended_false_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a14")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        ended_false = _post_raw(client, _raw_body("sess_a14", _one_high_turns(), call_ended=False))
        assert ended_false.status_code == 422
        _assert_envelope(ended_false.json(), code="A2002")
        empty = _post_raw(
            client,
            _raw_body("sess_a14b", []),
            key=str(uuid4()),
        )
        assert empty.status_code == 422
        _assert_envelope(empty.json(), code="A2002")
        done = [row for row in _qc_records(db) if row.qc_status == "DONE"]
        assert done == []
        assert _qc_records(db) == []
