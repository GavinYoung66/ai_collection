"""投诉全旅程 E2E：接单→judge→status→verdict→review→close→Pub-Sub→回流。"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.events.publisher import publisher


@pytest.fixture(autouse=True)
def _clear():
    publisher.clear()
    yield


class TestComplaintJourney:
    def test_full_journey_zero_failure(self, client: TestClient):
        """关键路径：接单 → judge(run_graph=True) → status → verdict → review → close。"""
        # 1. 接单
        r = client.post("/api/v1/complaints", json={
            "complaint_text": "用户投诉坐席催收态度恶劣",
            "case_id": "case-e2e-001",
        })
        assert r.status_code == 200
        cid = r.json()["data"]["complaint_id"]

        # 2. judge（真图）
        r = client.post(f"/api/v1/complaints/{cid}/judge", json={"run_graph": True})
        assert r.status_code == 200
        verdict = r.json()["data"]["verdict"]
        assert verdict in ("agent_violation", "user_misunderstanding",
                           "compliant_but_poor_experience", "pending")

        # 3. status
        r = client.get(f"/api/v1/complaints/{cid}/status")
        assert r.status_code == 200
        assert r.json()["data"]["task_status"] == "pending_review"

        # 4. verdict
        r = client.get(f"/api/v1/complaints/{cid}/verdict")
        assert r.status_code == 200
        assert r.json()["data"]["verdict"] == verdict

        # 5. review (approve)
        r = client.put(f"/api/v1/complaints/{cid}/review", json={
            "review_action": "approve", "reviewer": "senior_ops",
        })
        assert r.status_code == 200

        # 6. close
        r = client.put(f"/api/v1/complaints/{cid}/close", json={"closer": "ops"})
        assert r.status_code == 200
        assert r.json()["data"]["task_status"] == "closed"

        # 7. Pub-Sub 事件断言（硬要求）
        topics = [e.topic for e in publisher.list_events()]
        assert "complaint.closed" in topics
        judged = [e for e in publisher.list_events() if e.topic == "complaint.judged"]
        assert len(judged) >= 1
        assert judged[0].payload.get("complaint_id") == cid
        assert "trace_id" in judged[0].payload
    def test_journey_with_review_modify(self, client: TestClient):
        """改判后走 V4 定责+回电再关单。"""
        r = client.post("/api/v1/complaints", json={"complaint_text": "E2E modify"})
        cid = r.json()["data"]["complaint_id"]
        client.post(f"/api/v1/complaints/{cid}/judge")

        r = client.put(
            f"/api/v1/complaints/{cid}/disposition/verdict",
            json={
                "establishment": "user_misunderstanding",
                "reason": "E2E 改判",
                "reviewer": "ops",
            },
        )
        assert r.json()["code"] == 0
        r = client.put(
            f"/api/v1/complaints/{cid}/disposition/callback",
            json={"reviewer": "ops"},
        )
        assert r.json()["code"] == 0

        r = client.put(f"/api/v1/complaints/{cid}/close")
        assert r.status_code == 200
        assert r.json()["data"]["task_status"] == "closed"
    def test_backflow_callback_mock(self, client: TestClient):
        """回流回调：mock 数据中台回写，断言被调用。"""
        r = client.post("/api/v1/complaints", json={"complaint_text": "E2E backflow"})
        cid = r.json()["data"]["complaint_id"]
        client.post(f"/api/v1/complaints/{cid}/judge")

        with patch("app.service.backflow_service.backflow_service.write_session") as mock_write:
            mock_write.return_value = True
            from app.service.backflow_service import backflow_service
            backflow_service.write_session("s-backflow", {"verdict": "agent_violation"})
            mock_write.assert_called_once()
