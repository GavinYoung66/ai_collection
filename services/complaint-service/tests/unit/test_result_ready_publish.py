"""5.5：write_session 成功发 result.ready；失败不发。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.events.publisher import publisher
from app.graph.nodes import sample_backflow as sb_mod
from app.graph.nodes.sample_backflow import sample_backflow_node
from app.models.state import ComplaintSessionState, TaskStatus, VerdictDraft, VerdictType


def _state(**kw) -> ComplaintSessionState:
    base = dict(
        session_id="sid-rr",
        complaint_id="cid-rr",
        case_ids=["case-rr"],
        task_status=TaskStatus.resolved,
        verdict_draft=VerdictDraft(
            verdict=VerdictType.agent_violation, confidence=0.9, reasoning="x"
        ),
    )
    base.update(kw)
    return ComplaintSessionState(**base)


@pytest.fixture(autouse=True)
def _clean():
    publisher.clear()
    sb_mod.reset_result_ready_dedupe()
    yield
    publisher.clear()
    sb_mod.reset_result_ready_dedupe()


def test_write_session_persisted_publishes_result_ready():
    async def _call(server, tool, payload, **kwargs):
        if tool == "write_session":
            return {
                "success": True,
                "persisted": True,
                "case_id": "case-rr",
                "complaint_id": "cid-rr",
            }
        return {"success": True, "persisted": True}

    with patch("app.graph.nodes.sample_backflow.get_mcp_client") as gm:
        gm.return_value.call = AsyncMock(side_effect=_call)
        asyncio.run(sample_backflow_node(_state()))

    events = publisher.list_events("complaint.result.ready")
    assert len(events) == 1
    assert events[0].payload["case_id"] == "case-rr"
    assert events[0].payload["complaint_no"] == "cid-rr"


def test_write_session_not_persisted_no_result_ready():
    async def _call(server, tool, payload, **kwargs):
        return {"success": True, "persisted": False}

    with patch("app.graph.nodes.sample_backflow.get_mcp_client") as gm:
        gm.return_value.call = AsyncMock(side_effect=_call)
        asyncio.run(sample_backflow_node(_state()))

    assert publisher.list_events("complaint.result.ready") == []


def test_result_ready_idempotent_no_double_publish():
    async def _call(server, tool, payload, **kwargs):
        return {
            "success": True,
            "persisted": True,
            "case_id": "case-rr",
            "complaint_id": "cid-rr",
        }

    with patch("app.graph.nodes.sample_backflow.get_mcp_client") as gm:
        gm.return_value.call = AsyncMock(side_effect=_call)
        asyncio.run(sample_backflow_node(_state()))
        asyncio.run(sample_backflow_node(_state()))

    assert len(publisher.list_events("complaint.result.ready")) == 1
