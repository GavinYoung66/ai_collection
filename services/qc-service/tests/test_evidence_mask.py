"""QC-10.3：evidence_text 落库经 common.security.mask。"""
from __future__ import annotations

from uuid import uuid4

from app.models.qc import QcViolation
from app.repositories.quality_repo import QualityRepo
from tests.test_skip_audit import FakeSession


class TestEvidenceMask:
    async def test_persisted_evidence_has_no_full_phone_or_id(self):
        session = FakeSession()
        repo = QualityRepo(session)  # type: ignore[arg-type]
        record = type("R", (), {"id": uuid4()})()
        raw = "请回拨13812345678，身份证110101199001011234"
        row = await repo.add_violation(
            record,  # type: ignore[arg-type]
            severity="high",
            speaker="agent",
            evidence_text=raw,
        )
        assert isinstance(row, QcViolation)
        assert "13812345678" not in (row.evidence_text or "")
        assert "110101199001011234" not in (row.evidence_text or "")
        assert "****" in (row.evidence_text or "")
        assert session.added[0].evidence_text == row.evidence_text

    async def test_ac_induce_redline_tag_persists_as_null(self):
        session = FakeSession()
        repo = QualityRepo(session)  # type: ignore[arg-type]
        record = type("R", (), {"id": uuid4()})()
        row = await repo.add_violation(
            record,  # type: ignore[arg-type]
            severity="low",
            speaker="agent",
            redline_tag="induce",
            evidence_text="qc_ac_probe_word",
        )
        assert row.redline_tag is None
