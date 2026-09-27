"""min-loop: qc.check.completed 仅回流快照，不建投诉；详情走 HTTP。"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.events import subscriber as sub_mod
from app.events.subscriber import handle_qc_check_completed


def test_subscriber_module_has_no_complaint_client():
    src = inspect.getsource(sub_mod)
    assert "ComplaintClient" not in src
    assert "create_complaint" not in src


@pytest.mark.asyncio
async def test_handle_completed_http_enrich_no_complaint_fields():
    payload = {
        "case_id": "c1",
        "session_id": "s1",
        "qc_record_id": "q1",
        "trace_id": "tr1",
    }
    detail = {
        "qc_record_id": "q1",
        "session_id": "s1",
        "case_id": "c1",
        "compliance_score": 88,
        "risk": "medium",
        "grade": "PASS",
    }

    with (
        patch(
            "app.events.subscriber._confirm_qc_record",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.events.subscriber.QcClient.get_record",
            new=AsyncMock(return_value=detail),
        ),
        patch(
            "app.events.subscriber._update_snapshot_qc_cols",
            new=AsyncMock(return_value=True),
        ) as upd,
    ):
        out = await handle_qc_check_completed(payload)

    assert out == {
        "status": "ok",
        "case_id": "c1",
        "snapshot_updated": True,
    }
    # Second arg to update should carry HTTP-enriched score
    call_payload = upd.await_args.args[1]
    assert call_payload.get("score") == 88
    assert call_payload.get("risk") == "medium"
