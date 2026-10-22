"""QC-6.1 / QC-6.2 / QC-6.3：replay / raw / batch HTTP。"""
from __future__ import annotations

from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.config import qc_settings
from app.deps import get_rag_client, get_redis
from app.integrations.qc_session import QCSession
from app.integrations.rag_client import RagClient
from app.main import app
from app.models.qc import QcRecord
from app.repositories.job_store import QcJobStore, wait_retry_key
from app.routers.check import get_dp_client, get_llm_reviewer, get_quality_repo
from app.services.llm_reviewer import LLMReviewer
from common.db.session import get_session
from common.errors.codes import ERROR_MESSAGES
from tests.test_job_store import MemoryRedis

ENVELOPE_KEYS = {"code", "message", "data", "trace_id"}
A2003_MSG = ERROR_MESSAGES["A2003"]


class _OneOrNoneResult:
    """仅服务 replay 的 active 规则包读取（qc_rules）；其余 SQL 仍禁止执行。"""

    def __init__(self, row=None):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class ApiFakeSession:
    def __init__(self):
        self.added: list = []
        self.call_ids: set[str] = set()
        self.qc_by_session: dict[str, QcRecord] = {}

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def execute(self, stmt):
        # replay_session 会读一次 active 规则包；HTTP 测试双无 active 行 →
        # 管道按 v1.0 种子等价回退，其它 SQL 一律禁止。
        if "qc_rules" in str(stmt):
            return _OneOrNoneResult(None)
        raise AssertionError("SQL should not run in HTTP fakes")


class FakeQualityRepo:
    """HTTP 测试双：不把 call_ids/qc_by_session 钩进生产 QualityRepo。"""

    def __init__(self, session: ApiFakeSession, job_store: QcJobStore | None = None):
        self.session = session
        self.job_store = job_store

    async def call_record_exists(self, session_id: str) -> bool:
        return session_id in self.session.call_ids

    async def get_by_session_id(self, session_id: str) -> QcRecord | None:
        return self.session.qc_by_session.get(session_id)

    async def insert_record(self, **kwargs) -> QcRecord:
        row = QcRecord(
            session_id=kwargs["session_id"],
            compliance_score=kwargs["compliance_score"],
            deduction_total=kwargs.get("deduction_total"),
            risk_level=kwargs["risk_level"],
            qc_result=kwargs["qc_result"],
            qc_status=kwargs["qc_status"],
            grade=kwargs.get("grade"),
            case_id=kwargs.get("case_id"),
            agent_id=kwargs.get("agent_id"),
            high_count=kwargs.get("high_count", 0),
            med_count=kwargs.get("med_count", 0),
            low_count=kwargs.get("low_count", 0),
        )
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        self.session.add(row)
        self.session.qc_by_session[row.session_id] = row
        return row

    async def add_violation(self, record, **fields):
        self.session.add(("violation", getattr(record, "id", None), fields))
        return None

    async def record_skipped_rules(self, *, qc_record_id, skipped_rules=None):
        self.session.add(("skip", qc_record_id, list(skipped_rules or [])))
        return None


class FakeDp:
    def __init__(self, sessions: dict[str, QCSession]):
        self.sessions = sessions
        self.calls: list[str] = []

    async def get_qc_source(self, session_id: str) -> QCSession:
        self.calls.append(session_id)
        return self.sessions[session_id]


def _ready(**kwargs) -> dict:
    base = {
        "session_id": "sess_ok",
        "case_id": "case_ok",
        "call_ended": True,
        "text_ready": True,
        "debtor_name": "张三",
        "turns": [
            {
                "turn_id": "t1",
                "speaker": "agent",
                "text": "让你妈妈接电话",
                "turn_index": 1,
            }
        ],
        "result": None,
        "call_id": None,
    }
    base.update(kwargs)
    return base


async def _empty_llm(_messages):
    return "[]"


def _assert_envelope(body: dict, *, code: str | None = None) -> None:
    assert ENVELOPE_KEYS <= set(body)
    if code is not None:
        assert body["code"] == code


def _assert_no_score_field(data: dict | None) -> None:
    if isinstance(data, dict):
        assert "score" not in data


def _qc_records(session: ApiFakeSession) -> list[QcRecord]:
    return [obj for obj in session.added if isinstance(obj, QcRecord)]


def _client(session: ApiFakeSession, redis: MemoryRedis, dp: FakeDp, *, llm=None, rag=None) -> TestClient:
    async def _session():
        yield session

    async def _redis():
        return redis

    def _llm():
        return llm or LLMReviewer(invoke=_empty_llm)

    async def _quality():
        return FakeQualityRepo(session, QcJobStore(redis))

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    app.dependency_overrides[get_dp_client] = lambda: dp
    app.dependency_overrides[get_llm_reviewer] = _llm
    app.dependency_overrides[get_quality_repo] = _quality
    if rag is not None:
        app.dependency_overrides[get_rag_client] = lambda: rag
    return TestClient(app)


def _cleanup():
    from common.resilience import hooks
    from common.resilience._redis import reset_redis

    from app.events.resilience import clear_emitted

    app.dependency_overrides.clear()
    reset_redis()
    hooks.set_publisher(None)
    clear_emitted()


class TestReplayPath:
    def teardown_method(self):
        _cleanup()

    def test_not_ready_wait_text_no_qc_records(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_wait")
        redis = MemoryRedis(db=3)
        qc = QCSession.model_validate(
            {
                "session_id": "sess_wait",
                "case_id": "c1",
                "call_ended": False,
                "text_ready": False,
                "turns": [],
                "result": None,
            }
        )
        dp = FakeDp({"sess_wait": qc})
        client = _client(db, redis, dp)
        resp = client.post("/api/v1/qc/replay/sess_wait", json={})
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2003")
        assert body["code"] != "0"
        assert body["message"] == A2003_MSG
        assert body["message"] != "A2003"
        assert _qc_records(db) == []
        raw = redis.data.get("qc:job:sess_wait")
        assert raw is not None
        assert "WAIT_TEXT" in raw
        assert dp.calls == ["sess_wait"]
        data = body["data"]
        assert data["call_id"] is None
        assert data["call_id"] != "sess_wait"
        _assert_no_score_field(data)
        assert "skipped_rules" not in data

    def test_body_turns_rejected(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        client = _client(db, redis, dp)
        resp = client.post(
            "/api/v1/qc/replay/sess_ok",
            json={"turns": [{"turn_id": "t1", "speaker": "agent", "text": "x", "turn_index": 1}]},
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)

    def test_reviewed_without_force_is_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        row = QcRecord(
            session_id="sess_ok",
            compliance_score=90,
            risk_level="low",
            qc_result="compliant",
            qc_status="REVIEWED",
        )
        row.id = uuid4()
        db.qc_by_session["sess_ok"] = row
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        client = _client(db, redis, dp)
        resp = client.post("/api/v1/qc/replay/sess_ok", json={})
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)

    def test_success_qc_run_result_fields(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})
        client = _client(db, redis, dp)
        resp = client.post("/api/v1/qc/replay/sess_ok", json={})
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        for key in (
            "qc_record_id",
            "session_id",
            "case_id",
            "deduction_total",
            "compliance_score",
            "risk",
            "grade",
            "qc_result",
            "qc_status",
            "hits",
        ):
            assert key in data
        assert data["session_id"] == "sess_ok"
        assert data["deduction_total"] == 20
        assert data["compliance_score"] == 80
        # 一致性断言：compliance_score == max(0, 100 - deduction_total)
        assert data["compliance_score"] == max(0, 100 - data["deduction_total"])
        assert data["risk"] == "high"
        assert data["grade"] is None
        _assert_no_score_field(data)
        assert "skipped_rules" not in data
        assert data["call_id"] is None
        assert _qc_records(db)
        assert data["hits"]
        for hit in data["hits"]:
            assert "source" in hit
            assert "word" in hit
            assert "kb_snippet" in hit
            assert "points" in hit
            assert "policy_clause" in hit
            assert "policy_text" in hit
        stored = [item for item in db.added if isinstance(item, tuple) and item[0] == "violation"]
        assert stored
        for _kind, _rid, fields in stored:
            assert "source" in fields
            assert "word" in fields
            assert "kb_snippet" in fields
            assert "points" in fields
            assert fields["points"] == 20
            assert "policy_clause" in fields
            assert "policy_text" in fields

    def test_retry_max_inserts_partial_a2003(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_wait")
        redis = MemoryRedis(db=3)
        redis.data[wait_retry_key("sess_wait")] = str(qc_settings.QC_TEXT_READY_RETRY_MAX)
        qc = QCSession.model_validate(
            {
                "session_id": "sess_wait",
                "case_id": "c1",
                "call_ended": False,
                "text_ready": False,
                "turns": [],
                "result": None,
            }
        )
        dp = FakeDp({"sess_wait": qc})
        client = _client(db, redis, dp)
        resp = client.post("/api/v1/qc/replay/sess_wait", json={})
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2003")
        assert body["message"] == A2003_MSG
        assert body["message"] != "A2003"
        data = body["data"]
        assert data["qc_status"] == "PARTIAL"
        _assert_no_score_field(data)
        rows = _qc_records(db)
        assert len(rows) == 1
        assert rows[0].qc_status == "PARTIAL"

    def test_no_call_records_a2003_no_insert(self):
        db = ApiFakeSession()
        redis = MemoryRedis(db=3)
        qc = QCSession.model_validate(_ready(session_id="no_master"))
        dp = FakeDp({"no_master": qc})
        client = _client(db, redis, dp)
        resp = client.post("/api/v1/qc/replay/no_master", json={})
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2003")
        assert "通话主数据" in body["message"]
        _assert_no_score_field(body["data"])
        assert _qc_records(db) == []


class TestReplayRaw:
    def teardown_method(self):
        _cleanup()

    def test_no_call_records_a2003_no_insert(self):
        db = ApiFakeSession()
        redis = MemoryRedis(db=3)
        dp = FakeDp({})
        client = _client(db, redis, dp)
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "idem-1"},
            json=_ready(session_id="sess_missing"),
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2003")
        assert "通话主数据" in body["message"]
        assert _qc_records(db) == []

    def test_missing_idempotency_is_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = client.post("/api/v1/qc/replay/raw", json=_ready())
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)

    def test_call_ended_false_is_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _ready(call_ended=False)
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "idem-2"},
            json=payload,
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)

    def test_illegal_speaker_is_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = _ready()
        payload["turns"][0]["speaker"] = "robot"
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "idem-3"},
            json=payload,
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)

    def test_empty_turns_is_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "idem-empty"},
            json=_ready(turns=[]),
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)
        _assert_no_score_field(body["data"])

    def test_missing_session_id_is_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = {k: v for k, v in _ready().items() if k != "session_id"}
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "idem-sid"},
            json=payload,
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)

    def test_missing_case_id_is_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = {k: v for k, v in _ready().items() if k != "case_id"}
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "idem-cid"},
            json=payload,
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, code="A2002")
        assert "B0001" not in str(body)

    def test_missing_optional_metadata_is_not_a2002(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        payload = {
            "session_id": "sess_ok",
            "case_id": "case_ok",
            "call_ended": True,
            "turns": [
                {"turn_id": "t1", "speaker": "agent", "text": "您好", "turn_index": 1}
            ],
        }
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "idem-4"},
            json=payload,
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        _assert_no_score_field(body["data"])

    def test_same_idempotency_key_does_not_insert_twice(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        headers = {"X-Idempotency-Key": "idem-same"}
        first = client.post("/api/v1/qc/replay/raw", headers=headers, json=_ready())
        _assert_envelope(first.json(), code="0")
        assert len(_qc_records(db)) == 1
        second = client.post("/api/v1/qc/replay/raw", headers=headers, json=_ready())
        assert second.status_code == 200
        body = second.json()
        _assert_envelope(body, code="0")
        _assert_no_score_field(body["data"])
        assert len(_qc_records(db)) == 1

    def test_rate_limit_eleventh_is_b0006(self):
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        codes: list[tuple[int, str]] = []
        for i in range(12):
            resp = client.post(
                "/api/v1/qc/replay/raw",
                headers={"X-Idempotency-Key": f"k{i}", "X-User-Id": "u-rate"},
                json=_ready(),
            )
            body = resp.json()
            _assert_envelope(body)
            codes.append((resp.status_code, body["code"]))
        assert codes[:10] == [(200, "0")] * 10
        assert codes[10] == (429, "B0006")
        assert codes[11] == (429, "B0006")

    def test_rate_limit_redis_down_allows_without_b0006(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        class DownRedis(MemoryRedis):
            async def set(self, key, value, ex=None, nx=False, **kwargs):
                if nx:
                    raise RedisConnectionError("redis down")
                return await super().set(key, value, ex=ex, nx=False)

        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = DownRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        resp = client.post(
            "/api/v1/qc/replay/raw",
            headers={"X-Idempotency-Key": "k-down", "X-User-Id": "u-down"},
            json=_ready(),
        )
        body = resp.json()
        _assert_envelope(body)
        assert body["code"] != "B0006"
        assert resp.status_code != 429

    def test_rate_limit_hit_payload_includes_trace_id(self):
        from app.events.resilience import clear_emitted, emitted
        from app.main import wire_resilience_hooks

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
                    "X-Idempotency-Key": f"k-trace-{i}",
                    "X-User-Id": "u-rate-trace",
                    "X-Trace-Id": "trace-raw-11",
                },
                json=_ready(),
            )
        body = resp.json()
        _assert_envelope(body, code="B0006")
        assert resp.status_code == 429
        hits = [payload for name, payload in emitted if name == "qc.rate_limit.hit"]
        assert hits
        assert hits[0]["trace_id"] == "trace-raw-11"
        assert hits[0]["key"] == "qc:ratelimit:raw:u-rate-trace"
        assert hits[0]["reason"] == "rate_limited"
        assert "qc.rate_limit.hit" not in {ch for ch, _ in redis.published}

    def test_rate_limit_user_dimension_header_ip_anon(self):
        from types import SimpleNamespace

        from app.routers.check import _raw_rate_user

        header = SimpleNamespace(headers={"X-User-Id": "alice"}, client=SimpleNamespace(host="9.9.9.9"))
        assert _raw_rate_user(header) == "alice"
        ip_only = SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.8"))
        assert _raw_rate_user(ip_only) == "10.0.0.8"
        anon = SimpleNamespace(headers={}, client=None)
        assert _raw_rate_user(anon) == "anon"

    def test_rate_limit_without_user_header_uses_client_ip_key(self):
        from app.events.resilience import clear_emitted, emitted
        from app.main import wire_resilience_hooks

        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        wire_resilience_hooks()
        clear_emitted()
        client = _client(db, redis, FakeDp({}))
        for i in range(11):
            resp = client.post(
                "/api/v1/qc/replay/raw",
                headers={"X-Idempotency-Key": f"k-ip-{i}", "X-Trace-Id": "trace-ip"},
                json=_ready(),
            )
        _assert_envelope(resp.json(), code="B0006")
        hits = [payload for name, payload in emitted if name == "qc.rate_limit.hit"]
        assert hits
        key = hits[0]["key"]
        assert key.startswith("qc:ratelimit:raw:")
        assert key != "qc:ratelimit:raw:anon"
        assert "u-rate" not in key
        assert hits[0]["reason"] == "rate_limited"
        assert hits[0]["trace_id"] == "trace-ip"


class TestBatch:
    def teardown_method(self):
        _cleanup()

    def test_over_100_is_a2004_and_processes_none(self):
        db = ApiFakeSession()
        redis = MemoryRedis(db=3)
        dp = FakeDp({})
        client = _client(db, redis, dp)
        ids = [f"s{i}" for i in range(101)]
        resp = client.post(
            "/api/v1/qc/check/batch",
            json={"batch_id": "b1", "session_ids": ids},
        )
        assert resp.status_code == 400
        body = resp.json()
        _assert_envelope(body, code="A2004")
        assert dp.calls == []
        assert db.added == []

    def test_partial_failures_go_to_rejected(self):
        db = ApiFakeSession()
        db.call_ids.add("ok1")
        db.call_ids.add("rev1")
        row = QcRecord(
            session_id="rev1",
            compliance_score=90,
            risk_level="low",
            qc_result="compliant",
            qc_status="REVIEWED",
        )
        row.id = uuid4()
        db.qc_by_session["rev1"] = row
        dp = FakeDp(
            {
                "ok1": QCSession.model_validate(_ready(session_id="ok1")),
                "rev1": QCSession.model_validate(_ready(session_id="rev1")),
                "miss": QCSession.model_validate(_ready(session_id="miss")),
            }
        )
        client = _client(db, MemoryRedis(db=3), dp)
        resp = client.post(
            "/api/v1/qc/check/batch",
            json={"batch_id": "b2", "session_ids": ["ok1", "rev1", "miss"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="0")
        data = body["data"]
        assert data["batch_id"] == "b2"
        assert len(data["accepted"]) == 1
        assert data["accepted"][0]["session_id"] == "ok1"
        assert "deduction_total" in data["accepted"][0]
        assert data["accepted"][0]["deduction_total"] == 20
        assert data["accepted"][0]["grade"] is None
        rejected = {item["session_id"]: item["code"] for item in data["rejected"]}
        assert rejected["rev1"] == "A2002"
        assert rejected["miss"] == "A2003"
        for item in data["rejected"]:
            assert "session_id" in item and "code" in item

    def test_ingest_path_not_registered(self):
        db = ApiFakeSession()
        redis = MemoryRedis(db=3)
        client = _client(db, redis, FakeDp({}))
        assert client.post("/api/v1/qc/check").status_code in {404, 405}
        assert client.post("/qc/check").status_code in {404, 405}


class TestA2011Envelope:
    def teardown_method(self):
        _cleanup()

    def test_llm_open_still_returns_scores(self, monkeypatch):
        from common.llm import reliability as reliability_mod
        from common.llm.reliability import set_default_service
        from common.resilience._redis import configure_redis

        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        configure_redis(redis)
        set_default_service("qc")
        reliability_mod._llm_breakers.clear()
        monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
        dp = FakeDp({"sess_ok": QCSession.model_validate(_ready())})

        async def _boom(model, messages, timeout_seconds):
            raise TimeoutError("timeout")

        client = _client(
            db,
            redis,
            dp,
            llm=LLMReviewer(invoke=_boom, job_store=QcJobStore(redis), retry_delay=0.0),
        )
        resp = client.post("/api/v1/qc/replay/sess_ok", json={})
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, code="A2011")
        assert body["data"] is not None
        assert body["data"]["compliance_score"] == 80
        assert body["data"].get("qc_status") != "PARTIAL"
        _assert_no_score_field(body["data"])


class TestKbSearchHttpFallback:
    def teardown_method(self):
        _cleanup()

    def test_search_failure_http_200_not_partial(self, monkeypatch):
        monkeypatch.setattr(qc_settings, "QC_R14_RAG_ENABLED", True)
        db = ApiFakeSession()
        db.call_ids.add("sess_ok")
        redis = MemoryRedis(db=3)
        qc = QCSession.model_validate(
            _ready(
                turns=[
                    {
                        "turn_id": "t1",
                        "speaker": "agent",
                        "text": "再不还就起诉你，还要强制执行",
                        "turn_index": 1,
                    }
                ]
            )
        )
        dp = FakeDp({"sess_ok": qc})

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                raise httpx.ConnectError("rag kb down")
            return httpx.Response(200, text="")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        rag = RagClient(base_url="http://rag-service:8000", retry_delay=0, client=http)
        client = _client(db, redis, dp, rag=rag)
        resp = client.post("/api/v1/qc/replay/sess_ok", json={})
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body)
        assert body["code"] in {"0", "A2011"}
        assert body["data"] is not None
        assert body["data"].get("qc_status") != "PARTIAL"
        assert body["data"]["compliance_score"] is not None
        _assert_no_score_field(body["data"])
