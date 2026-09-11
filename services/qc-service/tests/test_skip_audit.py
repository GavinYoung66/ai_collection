"""QC-2.4：空 skip 仍写 audit_logs；回源 Redis → audit → []。"""
from __future__ import annotations

from uuid import uuid4

from app.repositories.job_store import QcJobStore
from app.repositories.quality_repo import SKIP_ACTION, SKIP_RESOURCE_TYPE, QualityRepo
from common.db.models.audit import AuditLog
from tests.test_job_store import MemoryRedis


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def execute(self, stmt):
        logs = [obj for obj in self.added if isinstance(obj, AuditLog)]
        return FakeResult(logs[-1] if logs else None)


class TestSkipAudit:
    async def test_empty_skipped_rules_still_writes_audit(self):
        session = FakeSession()
        repo = QualityRepo(session)  # type: ignore[arg-type]
        rid = uuid4()
        log = await repo.record_skipped_rules(qc_record_id=rid, skipped_rules=[])
        assert log.action == SKIP_ACTION
        assert log.resource_type == SKIP_RESOURCE_TYPE
        assert log.resource_id == str(rid)
        assert log.detail["skipped_rules"] == []
        assert log.user_id is None
        assert any(isinstance(x, AuditLog) for x in session.added)

    async def test_load_prefers_redis_then_audit_then_empty(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        session = FakeSession()
        repo = QualityRepo(session, job_store=store)  # type: ignore[arg-type]
        rid = uuid4()
        sid = "sess-skip"

        assert await repo.load_skipped_rules(session_id=sid, qc_record_id=rid) == []

        await repo.record_skipped_rules(qc_record_id=rid, skipped_rules=["R14"])
        assert await repo.load_skipped_rules(session_id=sid, qc_record_id=rid) == ["R14"]

        await store.set_done(sid, qc_record_id=rid, skipped_rules=["R01", "R10"])
        assert await repo.load_skipped_rules(session_id=sid, qc_record_id=rid) == ["R01", "R10"]

        await store.delete_job(sid)
        assert await repo.load_skipped_rules(session_id=sid, qc_record_id=rid) == ["R14"]

    def test_qc_records_model_has_no_skipped_rules_column(self):
        from sqlalchemy import inspect

        from app.models.qc import QcRecord

        assert "skipped_rules" not in {c.key for c in inspect(QcRecord).columns}
