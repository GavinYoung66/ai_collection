"""模块7 API 路由测试（12 路由 + 边界）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes import mode as mode_route
from app.events.publisher import publisher
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    publisher.clear()
    return TestClient(app)


def _create(client: TestClient, text: str = "投诉催收") -> str:
    r = client.post("/api/v1/complaints", json={"complaint_text": text})
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert isinstance(r.json()["code"], int)
    return r.json()["data"]["complaint_id"]


def test_create_and_list(client: TestClient):
    cid = _create(client)
    r = client.get("/api/v1/complaints")
    assert r.json()["code"] == 0
    assert any(item["complaint_id"] == cid for item in r.json()["data"]["items"])


def test_list_filter_by_task_status(client: TestClient):
    """F1 复核工作台：task_status 单值过滤，total 与 items 均为筛后结果。"""
    r = client.post("/api/v1/complaints", json={"complaint_text": "投诉催收"})
    cid = r.json()["data"]["complaint_id"]
    status = r.json()["data"]["task_status"]

    r = client.get("/api/v1/complaints", params=[("task_status", status)])
    assert r.json()["code"] == 0
    items = r.json()["data"]["items"]
    assert any(item["complaint_id"] == cid for item in items)
    assert all(item["task_status"] == status for item in items)
    assert r.json()["data"]["total"] >= len(items)  # total 为筛后全量计数，items 为当前页切片

    # 不相交集合：用终态过滤必然不含该新单
    r2 = client.get("/api/v1/complaints", params=[("task_status", "resolved")])
    assert r2.json()["code"] == 0
    assert all(item["task_status"] == "resolved" for item in r2.json()["data"]["items"])
    assert not any(item["complaint_id"] == cid for item in r2.json()["data"]["items"])


def test_list_filter_by_multiple_task_status(client: TestClient):
    """重复查询参数（?task_status=a&task_status=b）按集合并集过滤。"""
    cid = _create(client)
    r = client.get(
        "/api/v1/complaints",
        params=[("task_status", "pending"), ("task_status", "pending_review")],
    )
    assert r.json()["code"] == 0
    items = r.json()["data"]["items"]
    assert {item["task_status"] for item in items} <= {"pending", "pending_review"}
    assert any(item["complaint_id"] == cid for item in items)


def test_list_filter_invalid_status_422(client: TestClient):
    """非法枚举值由 FastAPI/Pydantic 校验拦截（422）。"""
    r = client.get("/api/v1/complaints", params=[("task_status", "not_a_status")])
    assert r.status_code == 422


def test_detail_not_found(client: TestClient):
    r = client.get("/api/v1/complaints/nonexistent-id")
    assert r.json()["code"] == "A5001"
    assert isinstance(r.json()["code"], str)


def test_judge_stub_and_verdict(client: TestClient):
    """judge stub 后 GET verdict 须保留 stub 判责，不得误降级为 pending。"""
    cid = _create(client)
    r = client.post(f"/api/v1/complaints/{cid}/judge")
    assert r.json()["code"] == 0
    assert r.json()["data"]["verdict"] == "agent_violation"
    r = client.get(f"/api/v1/complaints/{cid}/verdict")
    assert r.json()["code"] == 0
    verdict = r.json()["data"]["verdict"]
    assert verdict == "agent_violation"
    assert verdict != "normal"
    assert r.json()["data"]["confidence"] == 0.92
    # COMP-03 stretch：evidence_refs / doc_id 可见
    data = r.json()["data"]
    assert "evidence_refs" in data
    assert "rag_doc_001" in data["evidence_refs"]
    assert "rag_doc_001" in data["rag_source_ids"]

    detail = client.get(f"/api/v1/complaints/{cid}").json()["data"]
    assert "evidence_refs" in detail
    assert "rag_doc_001" in detail["evidence_refs"]


def test_status_and_agent_logs(client: TestClient):
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    assert client.get(f"/api/v1/complaints/{cid}/status").json()["code"] == 0
    assert client.get(f"/api/v1/complaints/{cid}/agent-logs").json()["code"] == 0


def test_review(client: TestClient):
    cid = _create(client)
    r = client.put(
        f"/api/v1/complaints/{cid}/review",
        json={"review_action": "approve"},
    )
    assert r.json()["code"] == 0


def test_close_publishes_event(client: TestClient):
    """关单须先 resolve；旧 approve 须同步发出 callback.requested。"""
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    publisher.clear()
    assert client.put(
        f"/api/v1/complaints/{cid}/review",
        json={"review_action": "approve"},
    ).json()["code"] == 0
    assert len(publisher.list_events("callback.requested")) == 1
    r = client.put(f"/api/v1/complaints/{cid}/close", json={"reason": "done"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["event_published"] is True
    evs = publisher.list_events("complaint.closed")
    assert len(evs) == 1
    assert evs[0].payload["complaint_id"] == cid


def test_stats_not_swallowed_by_id(client: TestClient):
    """stats 固定路径必须先于 /{id} 命中。"""
    _create(client)
    r = client.get("/api/v1/complaints/stats")
    assert r.json()["code"] == 0
    data = r.json()["data"]
    bv = data["by_verdict"]
    assert set(bv.keys()) == {
        "agent_violation",
        "user_misunderstanding",
        "compliant_but_poor_experience",
        "pending",
    }
    assert "evidence_insufficient_rate" in data
    assert "mcp_failure_rate" in data


def test_consistency(client: TestClient):
    r = client.get("/api/v1/complaints/consistency")
    assert r.json()["code"] == 0
    assert "ai_human_consistency_rate" in r.json()["data"]


def test_mode_switch_success(client: TestClient):
    r = client.put(
        "/api/v1/complaints/mode",
        json={"mode": "multi_agent", "gray_percent": 100, "signers": ["ops", "tech"]},
    )
    assert r.json()["code"] == 0


def test_mode_switch_missing_signers_rejected(client: TestClient):
    r = client.put(
        "/api/v1/complaints/mode",
        json={"mode": "multi_agent", "gray_percent": 100},
    )
    assert r.status_code == 422


def test_mode_switch_fail_rollback_a5014(client: TestClient):
    class FailRedis:
        def get(self, k):
            return None

        def set(self, k, v):
            raise RuntimeError("redis down")

    prev = mode_route.get_redis_client()
    mode_route.set_redis_client(FailRedis())
    try:
        r = client.put(
            "/api/v1/complaints/mode",
            json={"mode": "single_agent", "gray_percent": 50, "signers": ["ops", "tech"]},
        )
        assert r.json()["code"] == "A5014"
    finally:
        mode_route.set_redis_client(prev)


def test_response_envelope(client: TestClient):
    r = client.post("/api/v1/complaints", json={"complaint_text": "x"})
    j = r.json()
    assert set(j.keys()) >= {"code", "message", "data", "trace_id"}


def test_demo_seed_not_swallowed_by_id(client: TestClient):
    """POST /demo/seed 必须先于 /{id} 命中，不得返回 A5001。"""
    r = client.post("/api/v1/complaints/demo/seed")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    ids = body["data"]["complaint_ids"]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert "A5001" not in str(body)
    assert all(isinstance(cid, str) and cid for cid in ids)
    items = body["data"]["items"]
    scenarios = {it["scenario"] for it in items}
    assert "v2_pending" in scenarios
    pending_item = next(it for it in items if it["scenario"] == "v2_pending")
    assert pending_item["task_status"] == "pending_review"
    detail = client.get(f"/api/v1/complaints/{pending_item['complaint_id']}").json()["data"]
    assert detail["verdict"] == "pending"


def test_demo_seed_unique_across_calls(client: TestClient):
    first = client.post("/api/v1/complaints/demo/seed").json()["data"]["complaint_ids"]
    second = client.post("/api/v1/complaints/demo/seed").json()["data"]["complaint_ids"]
    assert len(first) == 3 and len(second) == 3
    assert set(first).isdisjoint(set(second))
    assert len(set(first) | set(second)) == 6


def test_trace_not_found_a5001(client: TestClient):
    r = client.get("/api/v1/complaints/nonexistent-trace-id/trace")
    assert r.json()["code"] == "A5001"


def test_trace_after_run_graph_has_agent_and_llm(client: TestClient):
    """真图后 /trace 落盘事件至少含 Agent 与 LLM；langsmith_url 可空。"""
    seed = client.post("/api/v1/complaints/demo/seed")
    assert seed.json()["code"] == 0
    cid = seed.json()["data"]["complaint_ids"][0]

    before = client.get(f"/api/v1/complaints/{cid}/trace").json()
    assert before["code"] == 0
    assert before["data"]["events"] == []

    judged = client.post(
        f"/api/v1/complaints/{cid}/judge",
        json={"run_graph": True},
    )
    assert judged.json()["code"] == 0
    assert judged.json()["data"]["task_status"] == "pending_review"

    tr = client.get(f"/api/v1/complaints/{cid}/trace").json()
    assert tr["code"] == 0
    events = tr["data"]["events"]
    assert isinstance(events, list) and len(events) >= 2
    cats = {e["category"] for e in events}
    assert "Agent" in cats
    assert "LLM" in cats
    for e in events:
        assert "category" in e and "name" in e
        assert "started_at" in e and "duration_ms" in e and "status" in e
    # 无 Key/无模板时外链应为 null，不得报错
    assert tr["data"].get("langsmith_url") in (None, "")


def test_build_langsmith_url_requires_template(monkeypatch):
    """无 TEMPLATE 时即使有 run id 也不返回易 404 的默认链。"""
    from app.config import settings
    from app.service import trace as trace_mod

    monkeypatch.setattr(settings, "LANGSMITH_ENABLED", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "not-a-real-key")
    monkeypatch.setattr(settings, "LANGSMITH_PROJECT", "complaint-service")
    monkeypatch.setattr(settings, "LANGSMITH_TRACE_URL_TEMPLATE", "")
    monkeypatch.delenv("LANGSMITH_TRACE_URL_TEMPLATE", raising=False)
    assert trace_mod.build_langsmith_url("run-abc") is None

    monkeypatch.setattr(
        settings,
        "LANGSMITH_TRACE_URL_TEMPLATE",
        "https://example.test/p/{project}/r/{run_id}",
    )
    assert (
        trace_mod.build_langsmith_url("run-abc")
        == "https://example.test/p/complaint-service/r/run-abc"
    )


def test_mcp_llm_events_preserve_started_at():
    from datetime import datetime, timezone

    from app.service.trace import llm_entry_to_event, mcp_entry_to_event

    t0 = datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)
    mcp_ev = mcp_entry_to_event(
        {
            "server": "business_ticket",
            "tool": "query_case_info",
            "latency_ms": 12,
            "status": "success",
            "started_at": t0,
        }
    )
    llm_ev = llm_entry_to_event(
        {
            "agent_role": "reflection",
            "latency_ms": 30,
            "status": "success",
            "provider_path": "mock",
            "started_at": t0,
        }
    )
    assert mcp_ev["started_at"] == t0
    assert llm_ev["started_at"] == t0


def test_disposition_verdict_confirm_same_as_ai(client: TestClient):
    """确认与 AI 一致 → pending_callback，无需改判原因。"""
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    r = client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "agent_violation", "reviewer": "ops"},
    )
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["task_status"] == "pending_callback"
    assert body["data"]["establishment"] == "agent_violation"


def test_disposition_verdict_mismatch_requires_reason(client: TestClient):
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    r = client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "user_misunderstanding", "reviewer": "ops"},
    )
    assert r.json()["code"] == "A5002"
    r2 = client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={
            "establishment": "user_misunderstanding",
            "reason": "证据显示用户误解催收口径",
            "reviewer": "ops",
        },
    )
    assert r2.json()["code"] == 0
    assert r2.json()["data"]["task_status"] == "pending_callback"


def test_disposition_verdict_rejects_pending(client: TestClient):
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    r = client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "pending", "reviewer": "ops"},
    )
    assert r.json()["code"] == "A5002"


def test_disposition_verdict_from_ai_pending_needs_reason(client: TestClient):
    from app.models.state import VerdictType
    from app.service.complaint_service import complaint_service

    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    rec = complaint_service.get_or_raise(cid)
    # 模拟「AI 判为待定」：ai_verdict 是定责比对的基准快照，verdict 仅供展示
    rec["verdict"] = VerdictType.pending
    rec["ai_verdict"] = VerdictType.pending
    r = client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "agent_violation", "reviewer": "ops"},
    )
    assert r.json()["code"] == "A5002"
    r2 = client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={
            "establishment": "agent_violation",
            "reason": "补录音后认定违规",
            "reviewer": "ops",
        },
    )
    assert r2.json()["code"] == 0
    assert r2.json()["data"]["task_status"] == "pending_callback"


def test_disposition_callback_publishes_and_idempotent(client: TestClient):
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "agent_violation", "reviewer": "ops"},
    )
    publisher.clear()
    r = client.put(
        f"/api/v1/complaints/{cid}/disposition/callback",
        json={"reviewer": "ops", "note": "请今日回电安抚"},
    )
    assert r.json()["code"] == 0
    assert r.json()["data"]["task_status"] == "resolved"
    assert r.json()["data"]["callback_scheduled"] is True
    evs = publisher.list_events("callback.requested")
    assert len(evs) == 1
    assert evs[0].payload["complaint_id"] == cid
    assert evs[0].payload["establishment"] == "agent_violation"
    assert evs[0].payload["callback_required"] is True
    assert evs[0].payload["note"] == "请今日回电安抚"
    assert "trace_id" in evs[0].payload

    r2 = client.put(
        f"/api/v1/complaints/{cid}/disposition/callback",
        json={"reviewer": "ops"},
    )
    assert r2.json()["code"] == 0
    assert len(publisher.list_events("callback.requested")) == 1

    listed = client.get("/api/v1/complaints/events", params={"topic": "callback.requested"})
    assert listed.json()["code"] == 0
    assert listed.json()["data"]["total"] == 1


def test_close_blocked_until_callback(client: TestClient):
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    assert client.put(f"/api/v1/complaints/{cid}/close").json()["code"] == "A5002"
    client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "agent_violation"},
    )
    assert client.put(f"/api/v1/complaints/{cid}/close").json()["code"] == "A5002"
    client.put(f"/api/v1/complaints/{cid}/disposition/callback", json={})
    r = client.put(f"/api/v1/complaints/{cid}/close", json={"reason": "done"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["task_status"] == "closed"


def test_pending_callback_illegal_skip_to_closed(client: TestClient):
    """session 状态机：pending_callback 不可直接 closed。"""
    from app.models.state import TaskStatus
    from app.service.session_service import SessionService

    s = SessionService()
    sid = s.create_session("rx-skip", "case-skip")
    assert s.transition(sid, TaskStatus.evidence_gathering)
    assert s.transition(sid, TaskStatus.judging)
    assert s.transition(sid, TaskStatus.draft_generated)
    assert s.transition(sid, TaskStatus.pending_review)
    assert s.transition(sid, TaskStatus.pending_callback)
    assert s.transition(sid, TaskStatus.closed) is False
    assert s.transition(sid, TaskStatus.resolved) is True
    assert s.transition(sid, TaskStatus.closed) is True


def test_ordered_evidence_on_violation_detail(client: TestClient):
    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    # 定责前不返回违规清单
    d0 = client.get(f"/api/v1/complaints/{cid}").json()["data"]
    assert d0.get("ordered_evidence") == []
    client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "agent_violation"},
    )
    d1 = client.get(f"/api/v1/complaints/{cid}").json()["data"]
    assert len(d1["ordered_evidence"]) >= 2
    assert d1["ordered_evidence"][0]["ord"] == 1
    assert "penalty" not in d1
    assert "penalty_tier" not in d1


def test_qc_event_on_violation_callback_not_on_other(client: TestClient):
    # 违规：发 qc
    cid = _create(client, text="催收态度恶劣需质检")
    client.post(f"/api/v1/complaints/{cid}/judge")
    client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "agent_violation"},
    )
    publisher.clear()
    client.put(f"/api/v1/complaints/{cid}/disposition/callback", json={"reviewer": "ops"})
    qc = publisher.list_events("qc.violation.reported")
    assert len(qc) == 1
    payload = qc[0].payload
    assert payload["complaint_id"] == cid
    assert payload["deduction_reason"]
    assert "催收" in payload["complaint_summary"] or payload["complaint_summary"]
    assert isinstance(payload["evidence"], list)
    assert len(payload["evidence"]) >= 1
    assert "trace_id" in payload
    assert "penalty_tier" not in payload
    assert any(e.topic == "case.risk.upgraded" for e in publisher.list_events())

    # 非违规：不发 qc
    cid2 = _create(client, text="用户误解口径")
    client.post(f"/api/v1/complaints/{cid2}/judge")
    client.put(
        f"/api/v1/complaints/{cid2}/disposition/verdict",
        json={
            "establishment": "user_misunderstanding",
            "reason": "证据支持误解",
        },
    )
    publisher.clear()
    client.put(f"/api/v1/complaints/{cid2}/disposition/callback", json={})
    assert publisher.list_events("qc.violation.reported") == []
    assert len(publisher.list_events("callback.requested")) == 1


def test_qc_event_with_empty_evidence(client: TestClient):
    from app.service.complaint_service import complaint_service

    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    rec = complaint_service.get_or_raise(cid)
    rec["trace_events"] = []
    client.put(
        f"/api/v1/complaints/{cid}/disposition/verdict",
        json={"establishment": "agent_violation"},
    )
    publisher.clear()
    client.put(f"/api/v1/complaints/{cid}/disposition/callback", json={})
    qc = publisher.list_events("qc.violation.reported")
    assert len(qc) == 1
    # trace 清空后仍可从 regulation_refs / agent_logs 补薄证据；允许非空或空
    assert isinstance(qc[0].payload["evidence"], list)
    assert qc[0].payload["deduction_reason"]


def test_ordered_evidence_falls_back_to_refs_and_logs(client: TestClient):
    from app.service.complaint_service import complaint_service
    from app.service.ordered_evidence import build_ordered_evidence

    cid = _create(client)
    client.post(f"/api/v1/complaints/{cid}/judge")
    rec = complaint_service.get_or_raise(cid)
    rec["trace_events"] = []
    items = build_ordered_evidence(rec)
    assert len(items) >= 1
    cats = {it["category"] for it in items}
    assert cats & {"RAG", "Agent"}
