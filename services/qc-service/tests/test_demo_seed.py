"""qc-service 演示灌数：POST /api/v1/qc/demo/seed 与 GET /api/v1/qc/demo/pending。"""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.integrations.qc_session import QCSession
from app.main import app
from app.models.qc import QcRecord
from app.repositories.demo_repo import DemoSeedRepo
from app.repositories.demo_store import DEMO_SESSIONS_KEY, DemoSessionStore
from app.repositories.job_store import QcJobStore
from app.deps import get_redis
from app.routers.check import get_dp_client, get_llm_reviewer, get_quality_repo
from app.routers.demo import get_demo_seed_service
from common.db.session import get_session
from app.seeds.demo_scenarios import DEMO_SCENARIOS
from app.services.demo_seed_service import DemoSeedService
from app.services.llm_reviewer import LLMReviewer
from common.security.rbac import verify_token
from tests.test_job_store import MemoryRedis
from tests.test_replay_api import ENVELOPE_KEYS, ApiFakeSession, FakeDp, FakeQualityRepo

_DEMO_PATH_SEED = "/api/v1/qc/demo/seed"
_DEMO_PATH_PENDING = "/api/v1/qc/demo/pending"


class FakeDemoRepo(DemoSeedRepo):
    def __init__(self, session: ApiFakeSession):
        self.session = session
        self.reviewer_id = uuid4()
        self.calls: list[dict] = []
        self.turns: list[dict] = []

    async def ensure_reviewer(self):
        return self.reviewer_id

    async def insert_ended_call(self, **kwargs):
        self.session.call_ids.add(kwargs["session_id"])
        self.calls.append(kwargs)
        for turn in kwargs["turns"]:
            assert turn["speaker"] in {"ai", "user"}
            self.turns.append({"session_id": kwargs["session_id"], **turn})


async def _empty_llm(_messages):
    return "[]"


def _client(
    *,
    session: ApiFakeSession,
    redis: MemoryRedis,
    demo_repo: FakeDemoRepo,
    quality: FakeQualityRepo,
    dp: FakeDp | None = None,
) -> TestClient:
    store = DemoSessionStore(redis)
    svc = DemoSeedService(demo_repo, store, quality)

    async def _demo():
        return svc

    async def _session():
        yield session

    async def _redis():
        return redis

    async def _quality():
        return quality

    def _llm():
        return LLMReviewer(invoke=_empty_llm)

    app.dependency_overrides[get_demo_seed_service] = _demo
    app.dependency_overrides[get_quality_repo] = _quality
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    app.dependency_overrides[get_dp_client] = lambda: dp or FakeDp({})
    app.dependency_overrides[get_llm_reviewer] = _llm
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.clear()


def _qc_records(session: ApiFakeSession) -> list[QcRecord]:
    return [obj for obj in session.added if isinstance(obj, QcRecord)]


def _seed_qc_source(demo_repo: FakeDemoRepo, item: dict) -> QCSession:
    turns = [
        {
            "turn_id": f"t{turn['turn_number']}",
            "speaker": turn["speaker"],
            "text": turn["text"],
            "turn_index": turn["turn_number"],
        }
        for turn in demo_repo.turns
        if turn["session_id"] == item["session_id"]
    ]
    return QCSession.model_validate(
        {
            "session_id": item["session_id"],
            "case_id": item["case_id"],
            "call_id": item["call_id"],
            "call_ended": True,
            "text_ready": True,
            "turns": turns,
            "result": None,
            "debtor_name": "张三",
        }
    )


class TestDemoSeed:
    def teardown_method(self):
        _cleanup()

    def test_seed_creates_three_ended_calls_without_qc_records(self):
        session = ApiFakeSession()
        redis = MemoryRedis(db=3)
        demo_repo = FakeDemoRepo(session)
        quality = FakeQualityRepo(session, QcJobStore(redis))
        client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality)
        resp = client.post(_DEMO_PATH_SEED)
        assert resp.status_code == 200
        body = resp.json()
        assert ENVELOPE_KEYS <= set(body)
        assert body["code"] == "0"
        data = body["data"]
        assert len(data["session_ids"]) == 3
        assert len(data["sessions"]) == 3
        assert data["session_ids"] == [row["session_id"] for row in data["sessions"]]
        assert {row["scenario"] for row in data["sessions"]} == {"A", "B", "C"}
        assert len(demo_repo.calls) == 3
        for call in demo_repo.calls:
            assert call["duration_seconds"] > 0
            assert call["end_time"] is not None
            assert str(call["call_id"]) != call["session_id"]
        assert len(session.call_ids) == 3
        assert _qc_records(session) == []
        assert quality.session.qc_by_session == {}
        speakers = {turn["speaker"] for turn in demo_repo.turns}
        assert speakers <= {"ai", "user"}
        assert "agent" not in speakers
        payload = verify_token(data["token"])
        assert payload is not None
        assert payload["role"] == "qc_reviewer"
        assert payload["sub"] == data["reviewer_id"]
        UUID(data["reviewer_id"])

    def test_two_seeds_yield_six_distinct_session_ids(self):
        session = ApiFakeSession()
        redis = MemoryRedis(db=3)
        demo_repo = FakeDemoRepo(session)
        quality = FakeQualityRepo(session, QcJobStore(redis))
        client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality)
        first = client.post(_DEMO_PATH_SEED).json()["data"]["session_ids"]
        second = client.post(_DEMO_PATH_SEED).json()["data"]["session_ids"]
        combined = first + second
        assert len(combined) == 6
        assert len(set(combined)) == 6
        call_ids = [str(row["call_id"]) for row in demo_repo.calls]
        assert len(set(call_ids)) == 6
        assert set(call_ids).isdisjoint(set(combined))

    def test_scenarios_are_the_three_from_data_materials(self):
        assert [item["code"] for item in DEMO_SCENARIOS] == ["A", "B", "C"]
        assert DEMO_SCENARIOS[0]["label"] == "合规核身"
        blob_b = "".join(turn["text"] for turn in DEMO_SCENARIOS[1]["turns"])
        blob_c = "".join(turn["text"] for turn in DEMO_SCENARIOS[2]["turns"])
        assert "可以减免" in blob_b
        assert "我是法院的" in blob_c
        assert "找你家人" in blob_c or "妈妈" in blob_c


class TestDemoPending:
    def teardown_method(self):
        _cleanup()

    def test_empty_pending_is_200_not_a2005(self):
        session = ApiFakeSession()
        redis = MemoryRedis(db=3)
        demo_repo = FakeDemoRepo(session)
        quality = FakeQualityRepo(session, QcJobStore(redis))
        client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality)
        resp = client.get(_DEMO_PATH_PENDING)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0
        assert DEMO_SESSIONS_KEY not in redis.data or redis.data.get(DEMO_SESSIONS_KEY) in {None, "[]"}
        assert _qc_records(session) == []

    def test_seed_then_pending_lists_three(self):
        session = ApiFakeSession()
        redis = MemoryRedis(db=3)
        demo_repo = FakeDemoRepo(session)
        quality = FakeQualityRepo(session, QcJobStore(redis))
        client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality)
        seed = client.post(_DEMO_PATH_SEED).json()["data"]
        pending = client.get(_DEMO_PATH_PENDING).json()
        assert pending["code"] == "0"
        items = pending["data"]["items"]
        assert pending["data"]["total"] == 3
        assert {row["session_id"] for row in items} == set(seed["session_ids"])
        assert _qc_records(session) == []

    def test_replay_success_removes_session_from_pending(self):
        session = ApiFakeSession()
        redis = MemoryRedis(db=3)
        demo_repo = FakeDemoRepo(session)
        quality = FakeQualityRepo(session, QcJobStore(redis))
        client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality)
        seed = client.post(_DEMO_PATH_SEED).json()["data"]
        target = seed["sessions"][0]
        dp = FakeDp({target["session_id"]: _seed_qc_source(demo_repo, target)})
        client = _client(
            session=session,
            redis=redis,
            demo_repo=demo_repo,
            quality=quality,
            dp=dp,
        )
        replay = client.post(f"/api/v1/qc/replay/{target['session_id']}", json={})
        assert replay.status_code == 200
        replay_body = replay.json()
        assert replay_body["code"] in {"0", "A2011"}
        assert replay_body["data"]["qc_status"] != "PARTIAL"
        assert replay_body["data"]["call_id"] == target["call_id"]
        assert replay_body["data"]["call_id"] != target["session_id"]
        pending = client.get(_DEMO_PATH_PENDING).json()["data"]
        left = {row["session_id"] for row in pending["items"]}
        assert target["session_id"] not in left
        assert pending["total"] == 2
        assert set(seed["session_ids"]) - {target["session_id"]} == left

    def test_partial_record_stays_in_pending(self):
        session = ApiFakeSession()
        redis = MemoryRedis(db=3)
        demo_repo = FakeDemoRepo(session)
        quality = FakeQualityRepo(session, QcJobStore(redis))
        client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality)
        seed = client.post(_DEMO_PATH_SEED).json()["data"]
        sid = seed["session_ids"][1]
        row = QcRecord(
            session_id=sid,
            compliance_score=0,
            risk_level="low",
            qc_result="compliant",
            qc_status="PARTIAL",
        )
        row.id = uuid4()
        session.qc_by_session[sid] = row
        pending = client.get(_DEMO_PATH_PENDING).json()["data"]
        assert sid in {item["session_id"] for item in pending["items"]}
        assert pending["total"] == 3
