"""QC-6.4：台账 list / stats / detail。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.deps import get_quality_repo, get_rag_client, get_redis
from app.main import app
from app.models.qc import QcRecord, QcViolation
from app.repositories.job_store import QcJobStore
from common.security.mask import mask_text
from tests.test_job_store import MemoryRedis


class FakeRecordsRepo:
    def __init__(self):
        self.records: dict[str, QcRecord] = {}
        self.violations: dict[str, list[QcViolation]] = {}
        self.skipped: dict[str, list[str]] = {}
        self.job_store: QcJobStore | None = None

    async def get_by_session_id(self, session_id: str) -> QcRecord | None:
        return self.records.get(session_id)

    async def load_skipped_rules(self, *, session_id: str, qc_record_id) -> list[str]:
        if self.job_store is not None:
            job = await self.job_store.get_job(session_id)
            if job is not None and job.get("status") == "DONE" and "skipped_rules" in job:
                return list(job.get("skipped_rules") or [])
        return list(self.skipped.get(str(qc_record_id), []))

    async def list_violations(self, qc_record_id) -> list[QcViolation]:
        return list(self.violations.get(str(qc_record_id), []))

    async def list_records(self, *, page: int, page_size: int, **filters):
        rows = list(self.records.values())
        if filters.get("session_id"):
            rows = [r for r in rows if r.session_id == filters["session_id"]]
        if filters.get("risk"):
            rows = [r for r in rows if r.risk_level == filters["risk"]]
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

    async def stats(self, *, group_by: str, from_ts=None, to_ts=None):
        if not self.records:
            return []
        if group_by == "rule_id":
            return [{"key": "R12", "count": 1, "high_count": 1}]
        return [{"key": "x", "count": len(self.records)}]


def _record(**kwargs) -> QcRecord:
    score = kwargs.get("compliance_score", 80)
    row = QcRecord(
        session_id=kwargs.get("session_id", "sess_ok"),
        compliance_score=score,
        deduction_total=kwargs.get("deduction_total", 100 - score),
        risk_level=kwargs.get("risk_level", "high"),
        qc_result=kwargs.get("qc_result", "pending_review"),
        qc_status=kwargs.get("qc_status", "PENDING_REVIEW"),
        grade=kwargs.get("grade", "PASS"),
        high_count=kwargs.get("high_count", 1),
        med_count=0,
        low_count=0,
    )
    row.id = kwargs.get("id") or uuid4()
    row.case_id = kwargs.get("case_id")
    row.agent_id = kwargs.get("agent_id")
    row.create_time = kwargs.get("create_time") or datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    return row


def _client(repo: FakeRecordsRepo) -> TestClient:
    async def _repo():
        return repo

    app.dependency_overrides[get_quality_repo] = _repo
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.pop(get_quality_repo, None)
    app.dependency_overrides.pop(get_redis, None)
    app.dependency_overrides.pop(get_rag_client, None)


class TestRecordsList:
    def teardown_method(self):
        _cleanup()

    def test_empty_list_is_200_items_and_total_zero(self):
        client = _client(FakeRecordsRepo())
        resp = client.get("/api/v1/qc/records")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0
        assert body["data"]["page"] == 1
        assert body["data"]["page_size"] == 20
        assert "A2005" not in body["code"]

    def test_page_size_over_100_is_a2002(self):
        client = _client(FakeRecordsRepo())
        resp = client.get("/api/v1/qc/records", params={"page_size": 101})
        assert resp.status_code == 422
        assert resp.json()["code"] == "A2002"
        assert "B0001" not in resp.text

    def test_list_item_uses_risk_not_risk_level(self):
        repo = FakeRecordsRepo()
        repo.records["sess_ok"] = _record()
        client = _client(repo)
        resp = client.get("/api/v1/qc/records")
        item = resp.json()["data"]["items"][0]
        assert item["risk"] == "high"
        assert "risk_level" not in item
        assert "score" not in item
        assert item["deduction_total"] == 20
        assert item["compliance_score"] == 80
        assert item["grade"] is None
        assert "skipped_rules" not in item

    def test_grade_query_param_not_filtering(self):
        """4.3：grade 过滤参数退役（响应字段保留恒 null，不再作为过滤维度）。"""
        repo = FakeRecordsRepo()
        repo.records["sess_ok"] = _record(grade="WARNING")
        client = _client(repo)
        resp = client.get("/api/v1/qc/records", params={"grade": "WARNING"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 1
        assert len(body["data"]["items"]) == 1
        assert body["data"]["items"][0]["grade"] is None


class TestRecordsStats:
    def teardown_method(self):
        _cleanup()

    def test_empty_stats_is_200_items(self):
        client = _client(FakeRecordsRepo())
        resp = client.get("/api/v1/qc/records/stats", params={"group_by": "day"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["items"] == []
        assert body["data"]["group_by"] == "day"

    def test_illegal_group_by_is_a2002(self):
        client = _client(FakeRecordsRepo())
        resp = client.get("/api/v1/qc/records/stats", params={"group_by": "session"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "A2002"


class TestRecordsDetail:
    def teardown_method(self):
        _cleanup()

    def test_missing_row_is_a2005(self):
        client = _client(FakeRecordsRepo())
        resp = client.get("/api/v1/qc/records/sess_missing")
        assert resp.status_code == 404
        assert resp.json()["code"] == "A2005"

    def test_wait_text_redis_only_is_a2005(self):
        redis = MemoryRedis(db=3)
        redis.data["qc:job:sess_wait"] = (
            '{"status":"WAIT_TEXT","qc_record_id":null,"skipped_rules":[]}'
        )
        repo = FakeRecordsRepo()
        repo.job_store = QcJobStore(redis)
        client = _client(repo)
        resp = client.get("/api/v1/qc/records/sess_wait")
        assert resp.status_code == 404
        assert resp.json()["code"] == "A2005"

    def test_detail_has_qc_record_id_skipped_rules_and_masked_evidence(self):
        repo = FakeRecordsRepo()
        row = _record()
        phone = "13812345678"
        viol = QcViolation(
            qc_record_id=row.id,
            turn_id="t1",
            rule_id="R12",
            severity="high",
            redline_tag="fake_identity",
            speaker="agent",
            evidence_text=mask_text(f"请回拨{phone}"),
            source="rule",
            word=None,
            kb_snippet=None,
            points=20,
            policy_clause="X1.2",
            policy_text="不得冒充身份",
        )
        repo.records[row.session_id] = row
        repo.violations[str(row.id)] = [viol]
        repo.skipped[str(row.id)] = ["R14"]
        client = _client(repo)
        resp = client.get(f"/api/v1/qc/records/{row.session_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["qc_record_id"] == str(row.id)
        assert data["skipped_rules"] == ["R14"]
        assert data["risk"] == "high"
        assert data["deduction_total"] == 20
        assert data["compliance_score"] == 80
        assert data["grade"] is None
        assert "score" not in data
        assert "risk_level" not in data
        evidence = data["hits"][0]["evidence_text"]
        assert phone not in evidence
        assert "13812345678" not in resp.text
        assert data["hits"][0]["source"] == "rule"
        assert "word" in data["hits"][0]
        assert "kb_snippet" in data["hits"][0]
        assert data["hits"][0]["points"] == 20
        assert data["hits"][0]["policy_clause"] == "X1.2"
        assert data["hits"][0]["policy_text"] == "不得冒充身份"

    def test_detail_empty_skipped_rules_key_present(self):
        repo = FakeRecordsRepo()
        row = _record()
        repo.records[row.session_id] = row
        client = _client(repo)
        data = client.get(f"/api/v1/qc/records/{row.session_id}").json()["data"]
        assert "skipped_rules" in data
        assert data["skipped_rules"] == []
        assert data["hits"] == []

    def test_partial_wait_text_job_falls_through_to_audit(self):
        redis = MemoryRedis(db=3)
        redis.data["qc:job:sess_ok"] = (
            '{"status":"WAIT_TEXT","qc_record_id":null,"skipped_rules":[]}'
        )
        repo = FakeRecordsRepo()
        repo.job_store = QcJobStore(redis)
        row = _record(qc_status="PARTIAL")
        repo.records[row.session_id] = row
        repo.skipped[str(row.id)] = ["R14"]
        client = _client(repo)
        data = client.get(f"/api/v1/qc/records/{row.session_id}").json()["data"]
        assert data["skipped_rules"] == ["R14"]
