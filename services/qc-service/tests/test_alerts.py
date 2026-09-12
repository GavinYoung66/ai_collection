"""QC-8.1 / QC-8.2：告警落库门槛与主管 HTTP。"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.deps import get_alert_repo
from app.main import app
from app.models.qc import QcAlert
from app.repositories.alert_repo import AlertRepo
from app.services.ac_filter import WordHit
from app.services.alert_service import AlertService
from app.services.offline_qc_service import QcRunResult
from common.security.rbac import create_token
from tests.test_events_publish import _persist, _svc


def _hit(**kwargs) -> WordHit:
    defaults = dict(
        speaker="agent",
        word="x",
        severity="high",
        score_eligible=True,
        source="rule",
        evidence_text="证据13812341234",
    )
    defaults.update(kwargs)
    return WordHit(**defaults)  # type: ignore[arg-type]


def _token(role: str, *, sub: str | None = None) -> dict[str, str]:
    uid = sub or str(uuid4())
    return {"Authorization": f"Bearer {create_token({'sub': uid, 'role': role})}"}


class FakeAlertRepo:
    def __init__(self) -> None:
        self.rows: list[QcAlert] = []
        self.meta: dict[str, dict] = {}
        self.session = SimpleNamespace(commit=self._commit)

    async def _commit(self) -> None:
        return None

    async def insert_high(self, **kwargs) -> QcAlert:
        row = QcAlert(
            id=kwargs.get("alert_id") or uuid4(),
            qc_record_id=kwargs["qc_record_id"],
            violation_id=kwargs.get("violation_id"),
            level="high",
            status="NEW",
        )
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        row.create_time = datetime(2026, 9, 9, 10, 1, tzinfo=timezone.utc)
        self.rows.append(row)
        return row

    async def get(self, alert_id):
        for row in self.rows:
            if str(row.id) == str(alert_id):
                return row
        return None

    async def list_alerts(self, *, page, page_size, status=None, session_id=None):
        items = []
        for row in self.rows:
            meta = self.meta.get(str(row.id), {})
            if status and row.status != status:
                continue
            if session_id and meta.get("session_id") != session_id:
                continue
            items.append(
                AlertRepo._item(
                    row,
                    session_id=meta.get("session_id"),
                    evidence=meta.get("evidence"),
                )
            )
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total

    async def ack(self, row: QcAlert, *, acked_by):
        row.status = "ACKED"
        row.acked_by = acked_by
        row.acked_at = datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc)
        return row


def _client(repo: FakeAlertRepo) -> TestClient:
    async def _repo():
        return repo

    app.dependency_overrides[get_alert_repo] = _repo
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.clear()


class TestAlertPersist:
    async def test_r05_only_does_not_insert_qc_alerts(self):
        replay, _pub, _redis, db = _svc()
        hits = [_hit(rule_id="R05", severity="low", redline_tag="threat")]
        result = QcRunResult(
            code="0",
            compliance_score=98,
            risk_level="low",
            grade="EXCELLENT",
            qc_result="pending_review",
            qc_status="PENDING_REVIEW",
            hits=hits,
            alerts=AlertService().on_high_risk(hits),
        )
        await _persist(replay, result, session_id="sess_r05")
        assert [obj for obj in db.added if isinstance(obj, QcAlert)] == []

    async def test_agent_high_inserts_new_alert(self):
        replay, _pub, _redis, db = _svc()
        hits = [_hit(rule_id="R12", severity="high", redline_tag="fake_identity")]
        result = QcRunResult(
            code="0",
            compliance_score=80,
            risk_level="high",
            grade="PASS",
            qc_result="pending_review",
            qc_status="PENDING_REVIEW",
            hits=hits,
            alerts=AlertService().on_high_risk(hits),
        )
        await _persist(replay, result, session_id="sess_ok")
        rows = [obj for obj in db.added if isinstance(obj, QcAlert)]
        assert len(rows) == 1
        assert rows[0].level == "high"
        assert rows[0].status == "NEW"


class TestAlertHttp:
    def teardown_method(self):
        _cleanup()

    def test_empty_list_is_200_not_a2006(self):
        repo = FakeAlertRepo()
        client = _client(repo)
        resp = client.get("/api/v1/qc/alerts", headers=_token("SUPV"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0

    def test_r05_session_has_no_new_alert_in_list(self):
        repo = FakeAlertRepo()
        client = _client(repo)
        resp = client.get("/api/v1/qc/alerts", headers=_token("ADMIN"), params={"session_id": "sess_r05", "status": "NEW"})
        assert resp.status_code == 200
        assert resp.json()["data"]["items"] == []

    def test_agent_high_listed_as_new(self):
        repo = FakeAlertRepo()
        aid = uuid4()
        qid = uuid4()
        row = QcAlert(id=aid, qc_record_id=qid, level="high", status="NEW")
        row.create_time = datetime(2026, 9, 9, 10, 1, tzinfo=timezone.utc)
        repo.rows.append(row)
        repo.meta[str(aid)] = {"session_id": "sess_high", "evidence": "13812341234"}
        client = _client(repo)
        resp = client.get("/api/v1/qc/alerts", headers=_token("supv"), params={"session_id": "sess_high"})
        assert resp.status_code == 200
        item = resp.json()["data"]["items"][0]
        assert item["alert_id"] == str(aid)
        assert item["qc_record_id"] == str(qid)
        assert item["session_id"] == "sess_high"
        assert item["level"] == "high"
        assert item["status"] == "NEW"
        assert "13812341234" not in (item["evidence"] or "")
        assert "score" not in item

    def test_ack_success_then_second_is_409_a2006(self):
        repo = FakeAlertRepo()
        aid = uuid4()
        uid = str(uuid4())
        row = QcAlert(id=aid, qc_record_id=uuid4(), level="high", status="NEW")
        repo.rows.append(row)
        client = _client(repo)
        headers = _token("SUPV", sub=uid)
        first = client.post(f"/api/v1/qc/alerts/{aid}/ack", headers=headers)
        assert first.status_code == 200
        assert first.json()["data"]["status"] == "ACKED"
        assert first.json()["data"]["acked_by"] == uid
        second = client.post(f"/api/v1/qc/alerts/{aid}/ack", headers=headers)
        assert second.status_code == 409
        assert second.json()["code"] == "A2006"

    def test_ack_missing_is_409_a2006(self):
        client = _client(FakeAlertRepo())
        resp = client.post(f"/api/v1/qc/alerts/{uuid4()}/ack", headers=_token("ADMIN"))
        assert resp.status_code == 409
        assert resp.json()["code"] == "A2006"

    def test_ack_unauthenticated_is_401_b0002(self):
        client = _client(FakeAlertRepo())
        resp = client.post(f"/api/v1/qc/alerts/{uuid4()}/ack")
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"

    def test_ack_wrong_role_is_403_b0003(self):
        client = _client(FakeAlertRepo())
        resp = client.post(f"/api/v1/qc/alerts/{uuid4()}/ack", headers=_token("QC"))
        assert resp.status_code == 403
        assert resp.json()["code"] == "B0003"

    def test_page_size_over_100_and_bad_status_are_a2002(self):
        client = _client(FakeAlertRepo())
        over = client.get("/api/v1/qc/alerts", headers=_token("SUPV"), params={"page_size": 101})
        assert over.status_code == 422
        assert over.json()["code"] == "A2002"
        bad = client.get("/api/v1/qc/alerts", headers=_token("SUPV"), params={"status": "OPEN"})
        assert bad.status_code == 422
        assert bad.json()["code"] == "A2002"
