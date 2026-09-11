"""QC-13.1：qc-source 契约夹具钉死 DD-02 §4.6.1；本 change 无 DP ETL。"""
from __future__ import annotations

import json
from pathlib import Path

from app.integrations.qc_session import map_qc_source

_FIXTURE = Path(__file__).parent / "fixtures" / "qc_source.json"
_APP_ROOT = Path(__file__).resolve().parents[1] / "app"
_SERVICE_ROOT = Path(__file__).resolve().parents[1]

_SECTION_461_KEYS = (
    "text_ready",
    "call_status",
    "session_id",
    "case_id",
    "call_id",
    "call_ended",
    "agent_type",
    "started_at",
    "ended_at",
    "duration_seconds",
    "result",
    "debtor_name",
    "turns",
    "case_snapshot",
)

_ETL_MARKERS = (
    "dw.ods_",
    "dw.dws_",
    "dw.ads_",
    "ods_call_records",
    "wide_table",
    "CREATE TABLE dw.",
)


def test_fixture_has_result_and_omits_dial_history():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert "result" in payload
    assert payload["result"] is None
    assert "dial_history_24h" not in payload
    snap = payload["case_snapshot"]
    assert snap["ptp_amount"] is None
    assert snap["standard_answer_hit"] is None
    for key in _SECTION_461_KEYS:
        assert key in payload, key


def test_fixture_maps_without_inventing_dial_history():
    mapped = map_qc_source(json.loads(_FIXTURE.read_text(encoding="utf-8"))).to_mapped_dict()
    assert "result" in mapped
    assert mapped["result"] is None
    assert "dial_history_24h" not in mapped
    assert mapped["case_snapshot"]["ptp_amount"] is None
    assert mapped["case_snapshot"]["standard_answer_hit"] is None


def test_qc_service_has_no_dp_etl_or_wide_table():
    hits: list[str] = []
    for path in _APP_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in _ETL_MARKERS:
            if marker in text:
                hits.append(f"{path.relative_to(_SERVICE_ROOT)}:{marker}")
    assert hits == []
    assert not (_SERVICE_ROOT / "etl").exists()
    assert not any(_SERVICE_ROOT.rglob("*data_platform*"))
    assert not any(_SERVICE_ROOT.rglob("*wide_table*"))
