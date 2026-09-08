"""投诉服务压测 locustfile。

场景对齐 PRD 20.1/20.5（stub 路径）。
准入由 run_loadtest.sh 解析 CSV 校验（design D6）。
"""
from __future__ import annotations

import random

from locust import HttpUser, between, task


class ComplaintUser(HttpUser):
    wait_time = between(0.05, 0.2)
    host = "http://127.0.0.1:8006"

    @task(3)
    def create_and_judge(self):
        texts = [
            "用户投诉催收态度恶劣",
            "账单金额有疑问",
            "坐席服务不规范",
            "业务办理失败",
        ]
        with self.client.post(
            "/api/v1/complaints",
            json={"complaint_text": random.choice(texts)},
            name="POST /complaints",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"create failed: {resp.status_code}")
                return
            cid = (resp.json() or {}).get("data", {}).get("complaint_id")
            if not cid:
                resp.failure("no complaint_id")
                return
        with self.client.post(
            f"/api/v1/complaints/{cid}/judge",
            json={},
            name="POST /judge",
            catch_response=True,
        ) as jresp:
            if jresp.status_code != 200:
                jresp.failure(f"judge failed: {jresp.status_code}")

    @task(2)
    def query_status_verdict(self):
        r = self.client.post(
            "/api/v1/complaints",
            json={"complaint_text": "查询测试"},
            name="POST /complaints",
        )
        if r.status_code != 200:
            return
        cid = (r.json() or {}).get("data", {}).get("complaint_id")
        if not cid:
            return
        self.client.post(f"/api/v1/complaints/{cid}/judge", json={}, name="POST /judge")
        self.client.get(f"/api/v1/complaints/{cid}/status", name="GET /status")
        self.client.get(f"/api/v1/complaints/{cid}/verdict", name="GET /verdict")

    @task(1)
    def health_check(self):
        self.client.get("/health", name="GET /health")
