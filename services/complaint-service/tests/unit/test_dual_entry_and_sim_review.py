"""5.3 双入口 + 5.4 SIM_AUTO_REVIEW。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.events.publisher import publisher
from app.main import app
from app.service import complaint_service as cs_mod
from app.service.complaint_service import ComplaintService


@pytest.fixture()
def client() -> TestClient:
    publisher.clear()
    return TestClient(app)


def _async_saver(value):
    """`_get_graph_checkpointer` 已 async 化，替身必须可 await。"""
    async def _get():
        return value
    return _get


def test_http_create_does_not_auto_judge(client, monkeypatch):
    """5.3：POST /complaints 只建单，不自动 judge(run_graph=true)。"""
    judge_calls: list = []

    async def _spy_judge(*a, **k):
        judge_calls.append((a, k))
        raise AssertionError("HTTP create must not call judge_async")

    monkeypatch.setattr(
        cs_mod.complaint_service, "judge_async", AsyncMock(side_effect=_spy_judge)
    )

    resp = client.post(
        "/api/v1/complaints",
        json={"complaint_text": "HTTP 建单测试", "case_id": "case-http-001"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["complaint_id"]
    assert judge_calls == []


@pytest.mark.asyncio
async def test_sim_auto_review_true_resumes_when_breakpoint(monkeypatch):
    """SIM_AUTO_REVIEW=true 且 next 含 human_review → 同栈 resume。"""
    svc = ComplaintService()
    resumed: list[str] = []

    class _Graph:
        async def aget_state(self, config):
            return SimpleNamespace(next=("human_review",), values={})

        async def ainvoke(self, cmd, config=None):
            resumed.append(str(getattr(cmd, "resume", cmd)))

    monkeypatch.setattr(
        "app.config.settings.SIM_AUTO_REVIEW", True, raising=False
    )
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph",
        lambda checkpointer=None: _Graph(),
    )
    monkeypatch.setattr(
        cs_mod, "_get_graph_checkpointer", _async_saver(MagicMock()), raising=False
    )

    await svc._maybe_sim_auto_review("cid-sim-1")
    assert resumed == ["approve"]


@pytest.mark.asyncio
async def test_sim_auto_review_false_no_resume(monkeypatch):
    """SIM_AUTO_REVIEW=false → 不 resume。"""
    svc = ComplaintService()
    called = {"n": 0}

    class _Graph:
        async def aget_state(self, config):
            called["n"] += 1
            return SimpleNamespace(next=("human_review",), values={})

        async def ainvoke(self, *a, **k):
            called["n"] += 10

    monkeypatch.setattr(
        "app.config.settings.SIM_AUTO_REVIEW", False, raising=False
    )
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph",
        lambda checkpointer=None: _Graph(),
    )

    await svc._maybe_sim_auto_review("cid-sim-off")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_sim_auto_review_skips_when_no_breakpoint(monkeypatch):
    """开关开但 next 不含 human_review → 不 resume。"""
    svc = ComplaintService()
    resumed: list = []

    class _Graph:
        async def aget_state(self, config):
            return SimpleNamespace(next=(), values={})

        async def ainvoke(self, *a, **k):
            resumed.append(True)

    monkeypatch.setattr(
        "app.config.settings.SIM_AUTO_REVIEW", True, raising=False
    )
    monkeypatch.setattr(
        "app.graph.builder.build_complaint_graph",
        lambda checkpointer=None: _Graph(),
    )
    monkeypatch.setattr(
        cs_mod, "_get_graph_checkpointer", _async_saver(MagicMock()), raising=False
    )

    await svc._maybe_sim_auto_review("cid-sim-nobp")
    assert resumed == []
