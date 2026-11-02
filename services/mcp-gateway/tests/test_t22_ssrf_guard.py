"""T22：mcp-gateway 出口 URL 白名单与 SSRF 防护。

覆盖：
- 正常：白名单内的 http/https 主机可正常出站并解包信封
- 边界：非白名单主机/协议、回环/内网/保留 IP、userinfo、非法端口、空白名单均拒绝
- 异常：云元数据地址（169.254.169.254 / 100.100.100.200 / 元数据主机名）出站前拒绝并告警，
  且不发起任何连接
"""
import httpx
import pytest
from structlog.testing import capture_logs

from app.bridge_http import (
    BridgeHttpClient,
    allowed_hosts,
    allowed_schemes,
    validate_outbound_url,
)
from app.config import settings
from app.mcp_errors import MCPError


def _client(base_url: str, calls: list[str], payload: dict | None = None) -> BridgeHttpClient:
    """构造带 MockTransport 的客户端，记录所有真实出站 URL。"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=payload or {"code": "0", "data": {"ok": True}})

    return BridgeHttpClient(
        base_url, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


class TestAllowlistedOutbound:
    @pytest.mark.asyncio
    async def test_allowlisted_http_host_succeeds(self):
        calls: list[str] = []
        client = _client("http://rag-service:8000", calls)
        data = await client.request("GET", "/v1/kb/search")
        assert data == {"ok": True}
        assert calls == ["http://rag-service:8000/v1/kb/search"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_allowlisted_https_scheme_succeeds(self):
        calls: list[str] = []
        client = _client("https://data-platform", calls)
        data = await client.request("GET", "/health")
        assert data == {"ok": True}
        assert len(calls) == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_configurable_allowlist_accepts_custom_host(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "bridge.example.com")
        calls: list[str] = []
        client = _client("http://bridge.example.com:8080", calls)
        assert await client.request("GET", "/x") == {"ok": True}
        assert calls == ["http://bridge.example.com:8080/x"]
        await client.aclose()

    def test_allowlist_parsing_is_case_insensitive_and_multi(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "RAG-SERVICE, Data-Platform")
        assert allowed_hosts() == {"rag-service", "data-platform"}
        assert allowed_schemes() == {"http", "https"}

    def test_allowlist_config_accepts_iterables(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", ["rag-service", " dp "])
        assert allowed_hosts() == {"rag-service", "dp"}


class TestRejectedTargets:
    @pytest.mark.asyncio
    async def test_non_whitelisted_host_rejected(self):
        calls: list[str] = []
        client = _client("http://evil.example.com", calls)
        with capture_logs() as cap, pytest.raises(MCPError):
            await client.request("GET", "/steal")
        assert calls == []  # 出站前拒绝，不发起连接
        assert any(
            e.get("event") == "bridge_url_rejected"
            and "host not in allowlist" in str(e.get("reason"))
            for e in cap
        )
        await client.aclose()

    @pytest.mark.asyncio
    async def test_non_whitelisted_scheme_rejected(self):
        calls: list[str] = []
        client = _client("ftp://rag-service:8000", calls)
        with capture_logs() as cap, pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        assert any(
            e.get("event") == "bridge_url_rejected"
            and "scheme not allowed" in str(e.get("reason"))
            for e in cap
        )
        await client.aclose()

    @pytest.mark.asyncio
    async def test_loopback_ip_rejected_even_if_allowlisted(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "127.0.0.1")
        calls: list[str] = []
        client = _client("http://127.0.0.1:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_private_ip_rejected_even_if_allowlisted(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "10.0.0.5")
        calls: list[str] = []
        client = _client("http://10.0.0.5:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_ipv6_loopback_rejected(self):
        calls: list[str] = []
        client = _client("http://[::1]:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_unspecified_address_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "0.0.0.0")
        calls: list[str] = []
        client = _client("http://0.0.0.0:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_userinfo_rejected(self):
        calls: list[str] = []
        client = _client("http://user:pass@rag-service:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_invalid_port_rejected(self):
        calls: list[str] = []
        client = _client("http://rag-service:99999", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_missing_host_rejected(self):
        calls: list[str] = []
        client = _client("http://:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_host_allowlist_rejects_all(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "")
        calls: list[str] = []
        client = _client("http://rag-service:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_scheme_allowlist_rejects_all(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_SCHEMES", "")
        calls: list[str] = []
        client = _client("http://rag-service:8000", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/x")
        assert calls == []
        await client.aclose()


class TestCloudMetadataBlocked:
    @pytest.mark.asyncio
    async def test_metadata_ip_rejected_before_outbound(self, monkeypatch):
        """验证场景：base_url 指向 169.254.169.254 → 出站前拒绝并告警。"""
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "169.254.169.254")
        calls: list[str] = []
        client = _client("http://169.254.169.254", calls)
        with capture_logs() as cap, pytest.raises(MCPError):
            await client.request("GET", "/latest/meta-data/")
        assert calls == []
        rejected = [e for e in cap if e.get("event") == "bridge_url_rejected"]
        assert rejected and rejected[0]["path"] == "/latest/meta-data/"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_metadata_hostname_rejected_even_if_allowlisted(self, monkeypatch):
        monkeypatch.setattr(
            settings, "BRIDGE_ALLOWED_HOSTS", "metadata.google.internal"
        )
        calls: list[str] = []
        client = _client("http://metadata.google.internal", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/computeMetadata/v1/")
        assert calls == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_aliyun_metadata_ip_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "BRIDGE_ALLOWED_HOSTS", "100.100.100.200")
        calls: list[str] = []
        client = _client("http://100.100.100.200", calls)
        with pytest.raises(MCPError):
            await client.request("GET", "/latest/meta-data/")
        assert calls == []
        await client.aclose()

    def test_ipv4_mapped_metadata_rejected(self):
        with capture_logs() as cap, pytest.raises(MCPError):
            validate_outbound_url("http://[::ffff:169.254.169.254]/x", "/x")
        assert any(e.get("event") == "bridge_url_rejected" for e in cap)

    def test_link_local_ip_rejected(self):
        with pytest.raises(MCPError):
            validate_outbound_url("http://169.254.1.1/x", "/x")

    def test_public_ip_not_in_allowlist_rejected(self):
        with pytest.raises(MCPError):
            validate_outbound_url("http://93.184.216.34/x", "/x")
