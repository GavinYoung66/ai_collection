"""QC-8.3：复核 HTTP，路径参数 qc_record_id。"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.deps import get_redis, get_review_repo
from app.events.publisher import CHANNEL_REVIEW_DONE
from app.main import app
from app.models.qc import QcRecord, QcReview
from app.services.review_service import ReviewService
from common.security.rbac import create_token
from tests.test_job_store import MemoryRedis
from tests.test_records import _record


def _token(role: str, *, sub: str | None = None) -> dict[str, str]:
    uid = sub or str(uuid4())
    return {"Authorization": f"Bearer {create_token({'sub': uid, 'role': role})}"}


class FakeReviewRepo:
    def __init__(self) -> None:
        self.records: dict[str, QcRecord] = []
        self.by_id: dict[str, QcRecord] = {}
        self.reviews: list[QcReview] = []
        self.session = SimpleNamespace(commit=self._commit)

    async def _commit(self) -> None:
        return None

    def add(self, row: QcRecord) -> None:
        self.by_id[str(row.id)] = row

    async def get_record(self, qc_record_id):
        return self.by_id.get(str(qc_record_id))

    async def list_pending(self, *, page, page_size):
        rows = [r for r in self.by_id.values() if r.qc_status == "PENDING_REVIEW"]
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

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
        return row


def _client(repo: FakeReviewRepo, redis: MemoryRedis | None = None) -> TestClient:
    redis = redis or MemoryRedis(db=3)

    async def _repo():
        return repo

    async def _redis():
        return redis

    app.dependency_overrides[get_review_repo] = _repo
    app.dependency_overrides[get_redis] = _redis
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.clear()


class TestReviewHttp:
    def teardown_method(self):
        _cleanup()

    def test_pending_empty_is_200_not_a2007(self):
        client = _client(FakeReviewRepo())
        resp = client.get("/api/v1/qc/reviews/pending", headers=_token("QC"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0

    def test_correct_sets_reviewed_and_score(self):
        repo = FakeReviewRepo()
        redis = MemoryRedis(db=3)
        row = _record(compliance_score=80, qc_status="PENDING_REVIEW")
        repo.add(row)
        client = _client(repo, redis)
        resp = client.post(
            f"/api/v1/qc/reviews/{row.id}",
            headers=_token("SUPV"),
            json={"action": "CORRECT", "revised_score": 72, "reason": "改分"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["qc_status"] == "REVIEWED"
        assert data["compliance_score"] == 72
        assert data["qc_result"] == "violation"
        assert data["grade"] == "WARNING"
        assert "score" not in data
        assert data["risk"] == "high"
        assert row.qc_status == "REVIEWED"
        assert row.compliance_score == 72
        assert repo.reviews and repo.reviews[0].action == "CORRECT"
        channels = [ch for ch, _msg in redis.published]
        assert CHANNEL_REVIEW_DONE in channels

    def test_correct_100_sets_reviewed_and_score(self):
        repo = FakeReviewRepo()
        redis = MemoryRedis(db=3)
        row = _record(compliance_score=80, qc_status="PENDING_REVIEW")
        repo.add(row)
        client = _client(repo, redis)
        resp = client.post(
            f"/api/v1/qc/reviews/{row.id}",
            headers=_token("QC"),
            json={"action": "CORRECT", "revised_score": 100, "reason": "误报"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["qc_status"] == "REVIEWED"
        assert data["compliance_score"] == 100
        assert data["qc_result"] == "compliant"
        assert data["grade"] == "EXCELLENT"
        assert data["risk"] == "low"
        assert row.qc_status == "REVIEWED"
        assert row.compliance_score == 100
        assert row.qc_result == "compliant"

    def test_confirm_does_not_change_score(self):
        repo = FakeReviewRepo()
        row = _record(compliance_score=64, qc_status="PENDING_REVIEW")
        repo.add(row)
        client = _client(repo)
        resp = client.post(
            f"/api/v1/qc/reviews/{row.id}",
            headers=_token("QC"),
            json={"action": "CONFIRM", "reason": "扣分正确"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["qc_status"] == "REVIEWED"
        assert data["compliance_score"] == 64
        assert data["qc_result"] == "violation"
        assert data["grade"] == "WARNING"
        assert row.compliance_score == 64
        assert row.qc_result == "violation"
        assert repo.reviews and repo.reviews[0].action == "CONFIRM"
        assert repo.reviews[0].revised_score is None

    def test_reviewed_again_is_a2007(self):
        repo = FakeReviewRepo()
        row = _record(qc_status="REVIEWED")
        repo.add(row)
        client = _client(repo)
        resp = client.post(
            f"/api/v1/qc/reviews/{row.id}",
            headers=_token("QC"),
            json={"action": "CONFIRM"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "A2007"

    def test_missing_record_is_a2007(self):
        client = _client(FakeReviewRepo())
        resp = client.post(
            f"/api/v1/qc/reviews/{uuid4()}",
            headers=_token("QC"),
            json={"action": "DISMISS"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "A2007"

    def test_correct_out_of_range_is_a2008(self):
        repo = FakeReviewRepo()
        row = _record(qc_status="PENDING_REVIEW")
        repo.add(row)
        client = _client(repo)
        high = client.post(
            f"/api/v1/qc/reviews/{row.id}",
            headers=_token("QC"),
            json={"action": "CORRECT", "revised_score": 101},
        )
        assert high.status_code == 400
        assert high.json()["code"] == "A2008"
        missing = client.post(
            f"/api/v1/qc/reviews/{row.id}",
            headers=_token("QC"),
            json={"action": "CORRECT"},
        )
        assert missing.status_code == 400
        assert missing.json()["code"] == "A2008"
        assert row.qc_status == "PENDING_REVIEW"

    def test_sample_type_mappings_and_review_done(self):
        redis = MemoryRedis(db=3)
        assert ReviewService.sample_type("CONFIRM") == "positive"
        assert ReviewService.sample_type("DISMISS") == "negative"
        assert ReviewService.sample_type("CORRECT") == "corrected"
        mapping = {"CONFIRM": "positive", "DISMISS": "negative", "CORRECT": "corrected"}
        for action, sample in mapping.items():
            repo = FakeReviewRepo()
            row = _record(qc_status="PENDING_REVIEW")
            repo.add(row)
            client = _client(repo, redis)
            body = {"action": action}
            if action == "CORRECT":
                body["revised_score"] = 88
            resp = client.post(f"/api/v1/qc/reviews/{row.id}", headers=_token("qc"), json=body)
            assert resp.status_code == 200
            payload = None
            for channel, raw in redis.published:
                if channel == CHANNEL_REVIEW_DONE:
                    import json

                    payload = json.loads(raw)
            assert payload is not None
            assert payload["sample_type"] == sample
            assert payload["action"] == action
            redis.published.clear()

    def test_unauthenticated_is_401_b0002(self):
        client = _client(FakeReviewRepo())
        resp = client.get("/api/v1/qc/reviews/pending")
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"

    def test_wrong_role_is_403_b0003(self):
        client = _client(FakeReviewRepo())
        resp = client.get("/api/v1/qc/reviews/pending", headers=_token("ADMIN"))
        assert resp.status_code == 403
        assert resp.json()["code"] == "B0003"
