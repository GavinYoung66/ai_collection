"""API 端点集成测试：接单→judge(run_graph=True)→status→verdict→review→close。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.events.publisher import publisher


@pytest.fixture(autouse=True)
def _clear():
    publisher.clear()
    yield


class TestApiEndpoints:
    def test_full_lifecycle(self, client: TestClient):
        # 1. 接单
        r = client.post("/api/v1/complaints", json={"complaint_text": "测试投诉"})
        assert r.status_code == 200
        cid = r.json()["data"]["complaint_id"]

        # 2. judge (run_graph=True 走真图)
        r = client.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": True})
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["task_status"] == "pending_review"
        assert body["verdict"] is not None

        # 3. status
        r = client.get(f"/api/v1/complaints/{cid}/status")
        assert r.status_code == 200
        assert r.json()["data"]["task_status"] == "pending_review"

        # 4. verdict
        r = client.get(f"/api/v1/complaints/{cid}/verdict")
        assert r.status_code == 200
        assert "verdict" in r.json()["data"]

        # 5. review (approve)
        r = client.put(f"/api/v1/complaints/{cid}/review",
                       json={"review_action": "approve", "reviewer": "ops"})
        assert r.status_code == 200

        # 6. close
        r = client.put(f"/api/v1/complaints/{cid}/close", json={"closer": "ops"})
        assert r.status_code == 200
        assert r.json()["data"]["task_status"] == "closed"

        # 7. Pub-Sub 事件断言
        topics = [e.topic for e in publisher.list_events()]
        assert "complaint.closed" in topics

    def test_judge_stub_path(self, client: TestClient):
        r = client.post("/api/v1/complaints", json={"complaint_text": "stub 测试"})
        cid = r.json()["data"]["complaint_id"]
        r = client.post(f"/api/v1/complaints/{cid}/judge")  # 默认 run_graph=False
        assert r.status_code == 200
        assert r.json()["data"]["verdict"] == "agent_violation"

    def test_get_nonexistent(self, client: TestClient):
        r = client.get("/api/v1/complaints/nonexistent-id")
        assert r.status_code in (404, 200)
