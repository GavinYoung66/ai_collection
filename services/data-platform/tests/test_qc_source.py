"""P-01/P-10：qc-source 组装与夹具同构；无 ETL/宽表。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.qc_source import assemble_qc_source, map_speaker

_SH = ZoneInfo("Asia/Shanghai")


def test_map_speaker_ai_to_agent():
    assert map_speaker("ai") == "agent"
    assert map_speaker("human") == "agent"
    assert map_speaker("user") == "user"
    assert map_speaker("agent") == "agent"


def test_assemble_matches_section_461_keys_and_m1_nulls():
    started = datetime(2026, 9, 9, 10, 0, tzinfo=_SH)
    ended = datetime(2026, 9, 9, 10, 8, tzinfo=_SH)
    payload = assemble_qc_source(
        call={
            "call_id": "22222222-3333-4444-5555-666666666666",
            "session_id": "sess_fixture_001",
            "case_id": "11111111-2222-3333-4444-555555555555",
            "call_type": "ai",
            "call_status": "completed",
            "start_time": started,
            "end_time": ended,
            "duration_seconds": 480,
            "result": None,
            "customer_name": "张三",
            "overdue_amount": 3800.00,
            "overdue_days": 32,
        },
        turns=[
            {"turn_number": 1, "speaker": "ai", "text": "您好，请问是张三先生吗？"},
            {"turn_number": 2, "speaker": "user", "text": "是我。"},
        ],
    )
    for key in (
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
    ):
        assert key in payload, key
    assert "result" in payload
    assert payload["result"] is None
    assert "dial_history_24h" not in payload
    assert payload["call_ended"] is True
    assert payload["text_ready"] is True
    assert payload["turns"][0]["speaker"] == "agent"
    assert payload["turns"][0]["turn_id"] == "t1"
    snap = payload["case_snapshot"]
    assert snap["ptp_amount"] is None
    assert snap["standard_answer_hit"] is None
    assert snap["strategy_refs"] == []
    assert snap["due_amount"] == 3800.0
    assert snap["overdue_days"] == 32


def test_source_files_have_no_ods_or_wide_table():
    root = Path(__file__).resolve().parents[1] / "app"
    markers = ("dw.ods_", "dw.dws_", "wide_table", "ods_call_records")
    hits: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                hits.append(f"{path.name}:{marker}")
    assert hits == []
