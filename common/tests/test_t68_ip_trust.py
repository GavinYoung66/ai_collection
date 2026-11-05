"""T68 / 7.2：get_client_ip 代理头防护（TRUST_PROXY_HEADERS 开关 + 格式校验）单测。

验收判据：
- 默认 false：X-Real-IP/XFF 不被采信，回退 ASGI client（直连零回归）；
- true：合法 IPv4/IPv6 代理头采信；XFF 多级取首个合法项；
- 非法格式一律忽略回退，不抛错。
"""
from __future__ import annotations

from starlette.requests import Request

from common.config.settings import settings
from common.utils.ip import get_client_ip


def _request(headers: dict[str, str], client=("10.0.0.1", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client})


class TestGetClientIpTrust:
    def test_default_false_ignores_proxy_header(self, monkeypatch):
        """默认 false：代理头不被采信（伪造代理头无法污染限流/审计维度）。"""
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
        req = _request({"X-Real-IP": "1.2.3.4"})
        assert get_client_ip(req) == "10.0.0.1"

    def test_trust_enabled_accepts_real_ip(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        req = _request({"X-Real-IP": "1.2.3.4"})
        assert get_client_ip(req) == "1.2.3.4"

    def test_xff_takes_first_valid_entry(self, monkeypatch):
        """XFF 多级取首个合法项。"""
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        req = _request({"X-Forwarded-For": "8.8.8.8, 10.0.0.9"})
        assert get_client_ip(req) == "8.8.8.8"

    def test_invalid_format_falls_back_silently(self, monkeypatch):
        """非法格式：忽略不抛错，回退 ASGI client。"""
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        req = _request({"X-Real-IP": "not-an-ip"})
        assert get_client_ip(req) == "10.0.0.1"

    def test_xff_first_invalid_uses_next_valid(self, monkeypatch):
        """XFF 首项非法、次项合法：取首个合法项。"""
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        req = _request({"X-Forwarded-For": "junk-value, 8.8.4.4"})
        assert get_client_ip(req) == "8.8.4.4"

    def test_ipv6_accepted(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        req = _request({"X-Forwarded-For": "2001:db8::1, 1.2.3.4"})
        assert get_client_ip(req) == "2001:db8::1"

    def test_no_headers_fallback_unchanged(self):
        """无代理头：回退 ASGI client（直连场景零回归）。"""
        assert get_client_ip(_request({})) == "10.0.0.1"