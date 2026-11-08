"""T111：dp 出站 path 参数白名单（qc→dp 的 T79 口径单源，dp→qc/complaint 收敛）。

payload 的 session_id/complaint_id 直接拼 URL path，非法值（x/../y、含 ?/#）
被拒：qc_client 记日志返回 None（既有失败语义），complaint_client 显式抛
A2002（逃生舱宁可不发请求）。
"""
from __future__ import annotations

import pytest

from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

from app.integrations.complaint_client import ComplaintClient
from app.integrations.qc_client import QcClient


class TestT111QcClientWhitelist:
    @pytest.mark.asyncio
    async def test_invalid_session_id_rejected_no_request(self):
        """x/../y、含 ?/# 的 id 被拒：不发起请求，按既有失败语义返回 None。"""
        client = QcClient(base_url="http://qc-service:8000")
        for bad in ("x/../y", "a?b", "a#b", "a" * 65):
            assert await client.get_record(bad) is None

    @pytest.mark.asyncio
    async def test_empty_session_id_returns_none(self):
        """空 id 快路径（既有语义回归）。"""
        client = QcClient(base_url="http://qc-service:8000")
        assert await client.get_record("") is None


class TestT111ComplaintClientWhitelist:
    @pytest.mark.asyncio
    async def test_invalid_complaint_id_rejected(self):
        """非法 complaint_id 显式拒绝（A2002，逃生舱宁可不发请求）。"""
        client = ComplaintClient(base_url="http://complaint:8000")
        with pytest.raises(BizException) as ei:
            await client.judge_complaint(complaint_id="x/../y")
        assert ei.value.code == ErrorCode.A2002
        with pytest.raises(BizException):
            await client.review_approve(complaint_id="a?b")

    @pytest.mark.asyncio
    async def test_valid_id_passes_gate_to_request_layer(self, monkeypatch):
        """合法 id 通过白名单门进入请求层（此处以 _request mock 验证不抛）。"""
        captured: dict = {}

        async def fake_request(self, method, path, *, json_body=None):
            captured["path"] = path
            return {"complaint_id": "c1", "task_status": "pending_review"}

        monkeypatch.setattr(ComplaintClient, "_request", fake_request)
        client = ComplaintClient(base_url="http://complaint:8000")
        await client.judge_complaint(complaint_id="01HZZZZZZZZZZZZZZZZZZZZZZZ")
        assert captured["path"].endswith("/judge")