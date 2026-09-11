"""QC-9.1：call_qc 仅两查询；出参含 qc_record_id；无 ASR/会话工具。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.mcp.server import SERVER_NAME, CallQcServer
from app.mcp.tools import FORBIDDEN_TOOLS, TOOL_QUERY_QC_ALERTS, TOOL_QUERY_QC_VIOLATIONS
from app.models.qc import QcRecord, QcViolation


class FakeQualityRepo:
    def __init__(self) -> None:
        self.record: QcRecord | None = None
        self.violations: list[QcViolation] = []

    async def get_by_session_id(self, session_id: str) -> QcRecord | None:
        if self.record is not None and self.record.session_id == session_id:
            return self.record
        return None

    async def list_violations(self, qc_record_id) -> list[QcViolation]:
        return [row for row in self.violations if str(row.qc_record_id) == str(qc_record_id)]


class FakeAlertRepo:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def list_alerts(self, *, page, page_size, status=None, session_id=None):
        rows = list(self.items)
        if session_id:
            rows = [item for item in rows if item.get("session_id") == session_id]
        return rows, len(rows)


def _record(*, session_id: str, case_id) -> QcRecord:
    row = QcRecord(
        session_id=session_id,
        compliance_score=80,
        risk_level="high",
        qc_result="pending_review",
        qc_status="PENDING_REVIEW",
    )
    row.id = uuid4()
    row.case_id = case_id
    return row


class TestCallQcToolList:
    def test_only_two_query_tools(self):
        server = CallQcServer()
        names = [item["name"] for item in server.list_tools()]
        assert names == [TOOL_QUERY_QC_VIOLATIONS, TOOL_QUERY_QC_ALERTS]
        assert server.server_name == SERVER_NAME
        assert "query_call_asr" not in names
        assert "query_call_sessions" not in names
        assert FORBIDDEN_TOOLS.isdisjoint(set(names))
        assert not hasattr(CallQcServer, "query_call_asr")
        assert not hasattr(CallQcServer, "query_call_sessions")

    @pytest.mark.asyncio
    async def test_forbidden_tools_are_not_implemented(self):
        server = CallQcServer()
        for name in FORBIDDEN_TOOLS:
            with pytest.raises(ValueError):
                await server.call(name, {"case_id": "c", "session_id": "s"})


class TestCallQcQueries:
    @pytest.mark.asyncio
    async def test_violations_include_qc_record_id_and_fields(self):
        case_id = uuid4()
        quality = FakeQualityRepo()
        quality.record = _record(session_id="sess_mcp", case_id=case_id)
        row = QcViolation(
            qc_record_id=quality.record.id,
            rule_id="R12",
            redline_tag="fake_identity",
            speaker="agent",
            severity="high",
            evidence_text="请回拨13812341234我是法院的",
        )
        row.id = uuid4()
        row.hit_ts = datetime(2026, 9, 9, 10, 1, tzinfo=timezone.utc)
        row.create_time = row.hit_ts
        quality.violations = [row]
        server = CallQcServer(quality_repo=quality, alert_repo=FakeAlertRepo())
        items = await server.call(
            "query_qc_violations",
            {"case_id": str(case_id), "session_id": "sess_mcp"},
        )
        assert len(items) == 1
        item = items[0]
        assert item["qc_record_id"] == str(quality.record.id)
        assert item["qc_record_id"] != "qc_id"
        assert item["redline_tag"] == "fake_identity"
        assert item["violation_rule_id"] == "R12"
        assert "13812341234" not in item["snippet_text"]
        assert "****" in item["snippet_text"]
        assert item["ts"] == int(row.hit_ts.timestamp())
        assert "snippet_text" in item

    @pytest.mark.asyncio
    async def test_alerts_include_qc_record_id_and_alert_id(self):
        case_id = uuid4()
        quality = FakeQualityRepo()
        quality.record = _record(session_id="sess_mcp", case_id=case_id)
        alert_id = str(uuid4())
        alerts = FakeAlertRepo()
        alerts.items = [
            {
                "alert_id": alert_id,
                "qc_record_id": str(quality.record.id),
                "session_id": "sess_mcp",
                "evidence": "手机13812341234",
            }
        ]
        server = CallQcServer(quality_repo=quality, alert_repo=alerts)
        items = await server.call(
            "query_qc_alerts",
            {"case_id": str(case_id), "session_id": "sess_mcp"},
        )
        assert len(items) == 1
        assert items[0]["qc_record_id"] == str(quality.record.id)
        assert items[0]["alert_id"] == alert_id
        assert "13812341234" not in items[0]["evidence"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty_list_not_404(self):
        server = CallQcServer(quality_repo=FakeQualityRepo(), alert_repo=FakeAlertRepo())
        violations = await server.query_qc_violations({"case_id": str(uuid4()), "session_id": "missing"})
        alerts = await server.query_qc_alerts({"case_id": str(uuid4()), "session_id": "missing"})
        assert violations == []
        assert alerts == []

    @pytest.mark.asyncio
    async def test_case_mismatch_is_empty(self):
        quality = FakeQualityRepo()
        quality.record = _record(session_id="sess_mcp", case_id=uuid4())
        server = CallQcServer(quality_repo=quality, alert_repo=FakeAlertRepo())
        items = await server.query_qc_violations(
            {"case_id": str(uuid4()), "session_id": "sess_mcp"}
        )
        assert items == []
