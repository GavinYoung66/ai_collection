"""EventEnvelope whitelist tests (task 2.1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.eventbus.envelope import DATA_WHITELIST, EventEnvelope, build_envelope


def test_valid_data_accepted():
    env = build_envelope(
        "call.session.ended",
        {"case_id": "c1", "session_id": "s1"},
    )
    assert env.event_name == "call.session.ended"
    assert env.data == {"case_id": "c1", "session_id": "s1"}
    assert env.event_id.startswith("evt_")
    assert set(DATA_WHITELIST) >= set(env.data)


def test_all_whitelist_fields_ok():
    data = {k: f"v-{k}" for k in DATA_WHITELIST}
    env = EventEnvelope(
        event_id="evt_1",
        event_name="qc.check.completed",
        occurred_at="2026-09-27T00:00:00Z",
        trace_id="tr_1",
        data=data,
    )
    assert env.data == data


def test_illegal_data_field_rejected():
    with pytest.raises(ValidationError) as exc:
        build_envelope(
            "complaint.created",
            {"complaint_no": "CP-1", "conversation_text": "secret"},
        )
    assert "non-whitelist" in str(exc.value).lower() or "conversation_text" in str(
        exc.value
    )


def test_score_field_rejected():
    with pytest.raises(ValidationError):
        build_envelope("qc.check.completed", {"qc_record_id": "q1", "score": 99})


def test_reject_log_includes_event_id_and_trace_id(capsys):
    with pytest.raises(ValidationError):
        build_envelope(
            "complaint.created",
            {"complaint_no": "CP-1", "score": 1},
            event_id="evt_fixed",
            trace_id="tr_fixed",
        )
    text = capsys.readouterr().out + capsys.readouterr().err
    assert "evt_fixed" in text
    assert "tr_fixed" in text
    assert "score" in text or "rejected" in text.lower()


def test_parse_envelope_reject_keeps_ids(capsys):
    from common.eventbus.envelope import parse_envelope

    raw = {
        "event_id": "evt_in",
        "event_name": "etl.task.done",
        "occurred_at": "2026-09-27T00:00:00Z",
        "trace_id": "tr_in",
        "data": {"case_id": "c1", "conversation_text": "x"},
    }
    with pytest.raises(ValidationError):
        parse_envelope(raw)
    text = capsys.readouterr().out + capsys.readouterr().err
    assert "evt_in" in text
    assert "tr_in" in text


def test_peek_reject_meta_extracts_ids_and_fields():
    from common.eventbus.envelope import peek_reject_meta

    eid, tid, rejected = peek_reject_meta(
        {
            "event_id": "evt_peek",
            "trace_id": "tr_peek",
            "data": {"session_id": "s1", "score": 9, "conversation_text": "x"},
        }
    )
    assert eid == "evt_peek"
    assert tid == "tr_peek"
    assert "score" in rejected
    assert "conversation_text" in rejected
