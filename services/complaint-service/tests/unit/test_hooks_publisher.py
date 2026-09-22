"""G7 hooks→publisher 接线单测（spec session-service Requirement 3，tasks 7.2）。

覆盖：
- 触发降级 → publisher 收到 csmp.degrade.triggered，payload 含 trace_id/code/reason/node
- publisher 抛错 → hooks 回退 structlog，不抛异常、不阻断业务
"""
from __future__ import annotations

import pytest

from app.events.publisher import publisher
from app.main import wire_resilience_hooks
from app.models.state import ComplaintSessionState
from app.service.degrade_llm import node_degrade_fallback
from common.resilience import hooks


def _state() -> ComplaintSessionState:
    return ComplaintSessionState(
        session_id="s-hook",
        trace_id="trace-hook",
        complaint_id="c-hook",
        complaint_text="暴力催收投诉",
    )


@pytest.fixture(autouse=True)
def _wire_and_isolate():
    publisher.clear()
    wire_resilience_hooks()
    yield
    hooks.set_publisher(None)
    publisher.clear()


@pytest.mark.asyncio
async def test_degrade_event_routed_to_publisher_with_full_payload():
    update = await node_degrade_fallback("verdict", _state(), TimeoutError("llm timeout"))

    assert update["task_status"] == "pending_review"
    evs = publisher.list_events("csmp.degrade.triggered")
    assert evs, "publisher 未收到 csmp.degrade.triggered"
    payload = evs[0].payload
    assert payload["code"] == "A5017"
    assert payload["reason"] == "timeout"
    assert payload["node"] == "verdict"
    assert payload["trace_id"] == "trace-hook"


@pytest.mark.asyncio
async def test_publisher_failure_falls_back_without_raising(monkeypatch):
    def _boom(topic, payload):  # noqa: ARG001
        raise RuntimeError("publisher down")

    monkeypatch.setattr(publisher, "publish", _boom)

    # 不抛异常：hooks 降级 structlog，降级 update 照常返回
    update = await node_degrade_fallback("verdict", _state(), TimeoutError("llm timeout"))
    assert update["task_status"] == "pending_review"
    assert update["exception_info"]["error_type"] == "timeout"
