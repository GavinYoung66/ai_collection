"""T108/T109：top_k 收敛 + 下游 4xx 不可重试 + 出站 SSRF 校验全覆盖。

- T108（spec mcp-gateway：工具参数边界收敛）：越界 top_k 网关前置收敛为
  1–20，下游不返回参数错误；反复传 21 不打开 `csmp:mcp_degraded:rag_kb`；
  下游 422 标记不可重试（不重试、不计熔断失败）。
- T109（spec mcp-gateway：出站校验全覆盖）：qc_mcp_client 补过 SSRF 单源，
  指向云元数据地址的出站被拒。
"""
from __future__ import annotations

import httpx
import pytest

from app.bridge_http import BridgeHttpClient
from app.mcp_errors import MCPError
from app.tools.rag_kb import RagKbToolset


class TestT108TopKClamp:
    @pytest.mark.asyncio
    async def test_out_of_range_top_k_clamped(self, monkeypatch):
        """top_k=21/0/-1/"abc"/None 收敛到 [1,20]，下游不返回参数错误。"""
        captured: dict = {}

        async def fake_request(self, method, path, *, json_body=None, trace_id=""):
            captured["payload"] = json_body
            return {"results": []}

        monkeypatch.setattr(BridgeHttpClient, "request", fake_request)
        ts = RagKbToolset("http://rag-service:8000")
        for raw in (21, 0, -1, "abc", None):
            await ts.call_tool("search_regulations", {"query": "q", "top_k": raw})
            assert 1 <= captured["payload"]["top_k"] <= 20

    @pytest.mark.asyncio
    async def test_legal_top_k_passthrough(self, monkeypatch):
        """合法区间（5/20/1）原样透传（零回归）。"""
        captured: dict = {}

        async def fake_request(self, method, path, *, json_body=None, trace_id=""):
            captured["payload"] = json_body
            return {"results": []}

        monkeypatch.setattr(BridgeHttpClient, "request", fake_request)
        ts = RagKbToolset("http://rag-service:8000")
        for raw in (5, 20, 1):
            await ts.call_tool("search_regulations", {"query": "q", "top_k": raw})
            assert captured["payload"]["top_k"] == raw


class TestT108Bridge4xxNonRetryable:
    def _client(self, status: int) -> BridgeHttpClient:
        return BridgeHttpClient(
            "http://rag-service:8000",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(status, text="err")
                )
            ),
        )

    @pytest.mark.asyncio
    async def test_422_marked_non_retryable(self):
        """spec 场景：下游 422 参数校验错误 → 不可重试标记。"""
        with pytest.raises(MCPError) as ei:
            await self._client(422).request("POST", "/api/v1/kb/search", json_body={})
        assert ei.value.retryable is False

    @pytest.mark.asyncio
    async def test_500_still_retryable(self):
        """5xx 服务故障保持可重试语义不变。"""
        with pytest.raises(MCPError) as ei:
            await self._client(500).request("POST", "/api/v1/kb/search", json_body={})
        assert ei.value.retryable is True

    @pytest.mark.asyncio
    async def test_429_still_retryable(self):
        """下游 429 限流退避后可能恢复：保持可重试。"""
        with pytest.raises(MCPError) as ei:
            await self._client(429).request("POST", "/api/v1/kb/search", json_body={})
        assert ei.value.retryable is True


class TestT109OutboundValidation:
    @pytest.mark.asyncio
    async def test_metadata_address_rejected(self, monkeypatch):
        """spec 场景：指向云元数据地址的出站被拒（连接建立前）。"""
        from app.config import settings
        from app.qc_mcp_client import call_qc_remote

        monkeypatch.setattr(settings, "QC_SERVICE_URL", "http://169.254.169.254:8000")
        with pytest.raises(MCPError) as ei:
            await call_qc_remote(
                "query_qc_violations", {"case_id": "c1", "session_id": "s1"}
            )
        assert "outbound url rejected" in str(ei.value)

    @pytest.mark.asyncio
    async def test_loopback_address_rejected(self, monkeypatch):
        from app.config import settings
        from app.qc_mcp_client import call_qc_remote

        monkeypatch.setattr(settings, "QC_SERVICE_URL", "http://127.0.0.1:8000")
        with pytest.raises(MCPError) as ei:
            await call_qc_remote(
                "query_qc_violations", {"case_id": "c1", "session_id": "s1"}
            )
        assert "outbound url rejected" in str(ei.value)