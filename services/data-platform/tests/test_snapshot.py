"""快照表固定 17 列与序列化；回写请求体多传字段必须被拒。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from zoneinfo import ZoneInfo

from app.api.snapshot import SnapshotBackflowRequest
from app.services.snapshot import SNAPSHOT_COLUMNS, serialize_snapshot

_SH = ZoneInfo("Asia/Shanghai")

_EXPECTED_COLUMNS = [
    "case_id",
    "customer_name",
    "overdue_amount",
    "overdue_days",
    "session_id",
    "call_ended",
    "qc_record_id",
    "compliance_score",
    "risk_level",
    "has_high_alert",
    "complaint_id",
    "verdict",
    "task_status",
    "backflow_at",
    "product_type",
    "repayment_records",
    "create_time",
]


def test_snapshot_columns_exactly_seventeen_english_names():
    assert SNAPSHOT_COLUMNS == tuple(_EXPECTED_COLUMNS)
    assert len(SNAPSHOT_COLUMNS) == 17
    # 质检列沿用 qc_records 库列名，禁止退化为 score/risk
    assert "compliance_score" in SNAPSHOT_COLUMNS
    assert "risk_level" in SNAPSHOT_COLUMNS
    assert "score" not in SNAPSHOT_COLUMNS
    assert "risk" not in SNAPSHOT_COLUMNS


def test_serialize_snapshot_keeps_seventeen_keys_and_nulls():
    row = {
        "case_id": "11111111-2222-4333-8444-555555555501",
        "customer_name": "张*",
        "overdue_amount": Decimal("5280.00"),
        "overdue_days": 45,
        "session_id": "min-loop-sess-001",
        "call_ended": "1",
        "qc_record_id": None,
        "compliance_score": None,
        "risk_level": None,
        "has_high_alert": None,
        "complaint_id": None,
        "verdict": None,
        "task_status": None,
        "backflow_at": datetime(2026, 9, 23, 15, 30, tzinfo=_SH),
        "product_type": "credit_card",
        "repayment_records": [],
        "create_time": datetime(2026, 9, 20, 10, 0, tzinfo=_SH),
    }
    data = serialize_snapshot(row)
    assert list(data.keys()) == _EXPECTED_COLUMNS
    assert data["overdue_amount"] == 5280.0
    assert data["backflow_at"] == datetime(2026, 9, 23, 15, 30, tzinfo=_SH).isoformat()
    assert data["call_ended"] == "1"  # char(1)，不是 boolean
    for key in ("qc_record_id", "compliance_score", "risk_level", "has_high_alert",
                "complaint_id", "verdict", "task_status"):
        assert data[key] is None
    assert data["product_type"] == "credit_card"
    assert data["repayment_records"] == []
    assert data["create_time"] == datetime(2026, 9, 20, 10, 0, tzinfo=_SH).isoformat()
    # 不引入第 18 个键
    assert len(data) == 17


def test_serialize_snapshot_naive_datetime_isoformat():
    data = serialize_snapshot(
        {col: None for col in _EXPECTED_COLUMNS}
        | {"backflow_at": datetime(2026, 9, 23, tzinfo=timezone.utc)}
    )
    assert data["backflow_at"].startswith("2026-09-23T00:00:00")


def test_backflow_request_only_allows_three_fields():
    payload = SnapshotBackflowRequest(
        complaint_id="11111111-2222-4333-8444-555555555501",
        verdict="agent_violation",
        task_status="approved",
    )
    assert payload.complaint_id and payload.verdict and payload.task_status

    with pytest.raises(ValidationError):
        SnapshotBackflowRequest(
            complaint_id="c1",
            verdict="normal",
            task_status="done",
            risk_level="high",  # 多传质检列：禁止
        )

    with pytest.raises(ValidationError):
        SnapshotBackflowRequest(complaint_id="", verdict="normal", task_status="done")
