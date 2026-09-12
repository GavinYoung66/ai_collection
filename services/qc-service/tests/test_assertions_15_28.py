"""QC-11.2 轮次 3.3：DD-02 §8.2 断言 15–28。MOCK_LLM=1。不改 1–14 口径。"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import qc_settings
from app.deps import get_quality_repo
from app.events.publisher import CHANNEL_COMPLAINT_SUSPECTED
from app.integrations.guardrails_adapter import matcher_from_words
from app.integrations.qc_session import QCSession, map_qc_source
from app.integrations.rag_client import RagClient
from app.main import app
from app.models.qc import QcAlert, QcRecord
from app.repositories.job_store import QcJobStore, job_key
from app.repositories.quality_repo import QualityRepo
from app.seeds.qc_rules_v1 import V1_SEED
from app.services.ac_filter import SensitiveWord, WordHit, get_shared_ac
from app.services.dedup_ac import DedupACWindow
from app.services.llm_reviewer import LLMReviewer
from app.services.offline_qc_service import OfflineQcService
from app.services.r10 import evaluate_r10
from app.services.rule_engine import RuleEngine
from tests.test_assertions import (
    ENVELOPE_KEYS,
    _assert_envelope,
    _client,
    _cleanup,
    _post_raw,
    _published_channels,
    _raw_body,
    _token,
    _turn,
)
from tests.test_job_store import MemoryRedis
from tests.test_records import _record
from tests.test_replay_api import ApiFakeSession, FakeDp, _empty_llm, _qc_records
from tests.test_skip_audit import FakeSession

FIXTURE_QC_SOURCE = Path(__file__).resolve().parent / "fixtures" / "qc_source.json"
_APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
def _mock_llm_env(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("QC_MOCK_LLM", "true")
    monkeypatch.setattr("app.core.config.qc_settings.QC_MOCK_LLM", True)
    monkeypatch.setattr("app.services.llm_reviewer.qc_settings.QC_MOCK_LLM", True)


def _prime_ac(words: list[SensitiveWord]) -> None:
    ac = get_shared_ac()
    ac._meta = {item.word: item for item in words if item.word}
    ac._matcher = matcher_from_words([item.word for item in words if item.word])


def _assert_zero_kb_search(rag: RagClient | None = None) -> None:
    rag = rag or RagClient()
    assert qc_settings.QC_R14_RAG_ENABLED is False
    assert all("/kb/search" not in url for url in rag.recorded_urls())
    engine_src = (_APP / "services" / "rule_engine.py").read_text(encoding="utf-8")
    assert "/kb/search" not in engine_src


def _r14_empty_pack() -> dict:
    pack = deepcopy(V1_SEED)
    for item in pack["rules"]:
        if item["rule_id"] == "R14":
            item["params"]["keywords"] = []
    return pack


def _offline(*, rules=None) -> OfflineQcService:
    return OfflineQcService(
        llm=LLMReviewer(invoke=_empty_llm, retry_delay=0.0),
        rules=rules or RuleEngine(),
    )


def _session_dict(**kwargs) -> dict:
    base = {
        "session_id": "sess_a15",
        "case_id": "case_a15",
        "call_ended": True,
        "text_ready": True,
        "debtor_name": "张三",
        "turns": [_turn("agent", "您好", 1)],
        "result": None,
    }
    base.update(kwargs)
    return base


class TestAssertions15to28:
    def teardown_method(self):
        _cleanup()

    def test_assertion_15_suicide_comfort_no_agent_penalty(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a15")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _raw_body(
            "sess_a15",
            [
                _turn("user", "我想自杀", 1),
                _turn("agent", "您先别着急，注意身体，我们慢慢沟通", 2),
            ],
        )
        resp = _post_raw(client, payload)
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        assert data["compliance_score"] == 100
        assert CHANNEL_COMPLAINT_SUSPECTED not in _published_channels(redis)
        agent_hits = [hit for hit in data["hits"] if hit.get("speaker") == "agent" and hit.get("rule_id") == "R13"]
        assert agent_hits == []

    def test_assertion_16_complaint_then_collect_r13(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a16")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _raw_body(
            "sess_a16",
            [
                _turn("user", "我要去投诉你们", 1),
                _turn("agent", "赶紧还钱", 2),
            ],
        )
        resp = _post_raw(client, payload)
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        r13_agent = [
            hit
            for hit in data["hits"]
            if hit.get("rule_id") == "R13" and hit.get("speaker") == "agent"
        ]
        assert r13_agent
        assert data["compliance_score"] == 80
        assert data["compliance_score"] < 100

    def test_assertion_17_unended_replay_a2003_wait_text(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a17")
        redis = MemoryRedis(db=3)
        qc = QCSession.model_validate(
            {
                "session_id": "sess_a17",
                "case_id": "case_a17",
                "call_ended": False,
                "text_ready": False,
                "turns": [_turn("agent", "您好", 1)],
                "result": None,
            }
        )
        client = _client(db, redis, FakeDp({"sess_a17": qc}))
        resp = client.post("/api/v1/qc/replay/sess_a17", json={})
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2003")
        assert body["code"] != "A2002"
        assert _qc_records(db) == []
        raw = redis.data.get("qc:job:sess_a17")
        assert raw is not None
        assert "WAIT_TEXT" in raw

    def test_assertion_18_raw_no_call_records_a2003(self):
        db = ApiFakeSession()
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = _post_raw(client, _raw_body("sess_a18_missing", [_turn("agent", "您好请问方便吗", 1)]))
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2003")
        assert body["code"] != "A2002"
        assert _qc_records(db) == []

    def test_assertion_19_same_turn_ac_threat_r11_deducts_20(self):
        _prime_ac([SensitiveWord(word="必须还", category="threat", word_id="w-threat")])
        db = ApiFakeSession()
        db.call_ids.add("sess_a19")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = _post_raw(client, _raw_body("sess_a19", [_turn("agent", "你必须还", 1)]))
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        assert data["compliance_score"] == 80
        assert data["compliance_score"] != 72
        alerts = [obj for obj in db.added if isinstance(obj, QcAlert)]
        assert len(alerts) == 1
        assert alerts[0].level == "high"

    def test_assertion_20_no_ts_turn_index_diff_1_drops_second_ac(self):
        hits = [
            WordHit(
                speaker="agent",
                word="可以减免",
                severity="low",
                score_eligible=True,
                source="ac",
                word_id="w1",
                normalized_word="可以减免",
                turn_id="t1",
                turn_index=1,
            ),
            WordHit(
                speaker="agent",
                word="可以减免",
                severity="low",
                score_eligible=True,
                source="ac",
                word_id="w1",
                normalized_word="可以减免",
                turn_id="t2",
                turn_index=2,
            ),
        ]
        kept = DedupACWindow(30000).apply(hits)
        assert len(kept) == 1
        assert kept[0].turn_id == "t1"
        assert hits[0].hit_ts is None
        assert hits[0].start_offset_ms is None

    def test_assertion_21_suicide_comfort_no_alert_no_suspected(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a21")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _raw_body(
            "sess_a21",
            [
                _turn("user", "我想自杀", 1),
                _turn("agent", "您先别着急，注意身体，我们慢慢沟通", 2),
            ],
        )
        resp = _post_raw(client, payload)
        assert resp.status_code == 200
        _assert_envelope(resp.json(), code="0")
        assert [obj for obj in db.added if isinstance(obj, QcAlert)] == []
        assert CHANNEL_COMPLAINT_SUSPECTED not in _published_channels(redis)
        listed = client.get(
            "/api/v1/qc/alerts",
            headers=_token("SUPV"),
            params={"session_id": "sess_a21"},
        )
        assert listed.json()["data"]["items"] == []

    def test_assertion_22_r05_only_minus_2_no_alert(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_a22")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _raw_body(
            "sess_a22",
            [_turn("agent", "您好请问现在方便吗", 1)],
            dial_history_24h={"dial_count_24h": 5},
        )
        resp = _post_raw(client, payload)
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        r05 = [hit for hit in data["hits"] if hit.get("rule_id") == "R05"]
        assert r05
        assert data["compliance_score"] == 98
        assert data["qc_result"] == "pending_review"
        assert data["qc_status"] == "PENDING_REVIEW"
        assert [obj for obj in db.added if isinstance(obj, QcAlert)] == []
        assert CHANNEL_COMPLAINT_SUSPECTED not in _published_channels(redis)

    async def test_assertion_23_r14_skip_no_kb_search_not_partial(self):
        rag = RagClient()
        svc = _offline(rules=RuleEngine(_r14_empty_pack()))
        result = await svc.run(
            _session_dict(
                session_id="sess_a23",
                turns=[_turn("agent", "请尽快处理账单", 1)],
            )
        )
        assert "R14" in result.skipped_rules
        assert not any(hit.rule_id == "R14" for hit in result.hits)
        assert result.qc_status != "PARTIAL"
        assert result.qc_status == "PENDING_REVIEW"
        _assert_zero_kb_search(rag)

    async def test_assertion_24_r14_keyword_hit_zero_kb_search(self):
        rag = RagClient()
        db = ApiFakeSession()
        db.call_ids.add("sess_a24")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = _post_raw(client, _raw_body("sess_a24", [_turn("agent", "再不还就起诉你", 1)]))
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        r14 = [hit for hit in body["data"]["hits"] if hit.get("rule_id") == "R14"]
        assert r14
        _assert_zero_kb_search(rag)

    async def test_assertion_25_r10_completed_vs_missing_result(self):
        threat = WordHit(
            speaker="agent",
            word="x",
            severity="high",
            score_eligible=True,
            source="rule",
            rule_id="R11",
            redline_tag="threat",
            turn_id="t1",
        )
        hit, skipped = evaluate_r10([threat], "completed")
        assert skipped is False
        assert hit is not None
        assert hit.rule_id == "R10"
        miss, miss_skip = evaluate_r10([threat], None)
        assert miss_skip is True
        assert miss is None

        db = ApiFakeSession()
        db.call_ids.add("sess_a25")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        completed = _post_raw(
            client,
            _raw_body(
                "sess_a25",
                [_turn("agent", "我是法院的，过来配合调查", 1)],
                result="completed",
            ),
        )
        _assert_envelope(completed.json(), code="0")
        r10 = [h for h in completed.json()["data"]["hits"] if h.get("rule_id") == "R10"]
        assert r10

        svc = _offline()
        missing = await svc.run(
            _session_dict(
                session_id="sess_a25b",
                result=None,
                turns=[_turn("agent", "您好请尽快处理", 1)],
            )
        )
        assert "R10" in missing.skipped_rules
        assert not any(hit.rule_id == "R10" for hit in missing.hits)
        assert not any(hit.redline_tag == "unverified" for hit in missing.hits)

    def test_assertion_26_mock_dp_qc_source_skip_r05_r08(self):
        raw = json.loads(FIXTURE_QC_SOURCE.read_text(encoding="utf-8"))
        assert "result" in raw
        qc = map_qc_source(raw)
        mapped = qc.to_mapped_dict()
        assert "result" in mapped
        assert mapped["result"] is None
        assert "dial_history_24h" not in mapped
        assert mapped["case_snapshot"]["ptp_amount"] is None
        engine = RuleEngine().run(mapped)
        assert "R05" in engine.skipped_rules
        assert "R08" in engine.skipped_rules

        db = ApiFakeSession()
        db.call_ids.add(qc.session_id)
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({qc.session_id: qc}))
        resp = client.post(f"/api/v1/qc/replay/{qc.session_id}", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] in {"0", "A2011"}
        assert ENVELOPE_KEYS <= set(body)

    def test_assertion_27_r04_before_confirm_hits_after_shiwo_clears(self):
        before = RuleEngine().run(
            _session_dict(turns=[_turn("agent", "你欠款逾期了", 1), _turn("user", "啊？", 2)])
        )
        assert any(hit.rule_id == "R04" for hit in before.hits)
        after = RuleEngine().run(
            _session_dict(
                turns=[
                    _turn("agent", "请问是张先生吗", 1),
                    _turn("user", "是我", 2),
                    _turn("agent", "你欠款逾期了要还", 3),
                ]
            )
        )
        assert not any(hit.rule_id == "R04" for hit in after.hits)

    async def test_assertion_28_del_redis_job_get_skipped_from_audit(self):
        session = FakeSession()
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        quality = QualityRepo(session, store)  # type: ignore[arg-type]
        row = _record(session_id="sess_a28", qc_status="DONE", compliance_score=100, risk_level="low", grade="EXCELLENT")

        class DetailRepo:
            def __init__(self) -> None:
                self.job_store = store

            async def get_by_session_id(self, session_id: str) -> QcRecord | None:
                return row if session_id == row.session_id else None

            async def list_violations(self, qc_record_id):
                return []

            async def load_skipped_rules(self, *, session_id, qc_record_id):
                return await quality.load_skipped_rules(session_id=session_id, qc_record_id=qc_record_id)

        async def _repo():
            return DetailRepo()

        app.dependency_overrides[get_quality_repo] = _repo
        client = TestClient(app)

        await quality.record_skipped_rules(qc_record_id=row.id, skipped_rules=["R14"])
        await store.set_done(row.session_id, qc_record_id=row.id, skipped_rules=["R14"])
        assert job_key(row.session_id) in redis.data
        await store.delete_job(row.session_id)
        assert job_key(row.session_id) not in redis.data

        resp = client.get(f"/api/v1/qc/records/{row.session_id}")
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        assert body["data"]["skipped_rules"] == ["R14"]
        assert "score" not in body["data"]
