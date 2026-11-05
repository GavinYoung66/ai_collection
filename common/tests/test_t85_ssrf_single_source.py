"""T85 / 批次 10：validate_outbound_url 上移 common 单源 单测。

验收判据：
- complaint 出站点对 http://169.254.169.254/ 拒绝（云元数据 IP 无条件阻断）；
- 空 scheme 白名单 = 显式全拒（不回退默认——bridge 空配置语义一致）；
- allowed_hosts=None 跳过 host 白名单比对（仅安全阻断）；
- 公网地址放行。
"""
from __future__ import annotations


from common.security.ssrf import check_outbound_url


class TestCheckOutboundUrl:
    def test_metadata_ip_rejected_unconditionally(self):
        """http://169.254.169.254/ 拒绝（云元数据 IP，白名单不可豁免）。"""
        reason = check_outbound_url("http://169.254.169.254/", allowed_hosts={"*"})
        assert reason == "cloud metadata address"

    def test_metadata_hostname_rejected(self):
        reason = check_outbound_url("http://metadata.google.internal/")
        assert reason == "cloud metadata hostname"

    def test_private_ip_rejected(self):
        assert check_outbound_url("http://10.0.0.5/") == "private network address"
        assert check_outbound_url("http://127.0.0.1/") == "loopback address"

    def test_public_ip_allowed_without_host_allowlist(self):
        """公网 IP + allowed_hosts=None（跳过比对）：放行。"""
        assert check_outbound_url("https://8.8.8.8/") is None

    def test_empty_scheme_allowlist_rejects_all(self):
        """空 scheme 白名单 = 显式全拒（不回退默认——bridge 空配置语义一致）。"""
        assert check_outbound_url(
            "https://rag-service/health", allowed_schemes=set()
        ) is not None

    def test_userinfo_rejected(self):
        assert check_outbound_url("http://user:pass@rag-service/") is not None

    def test_host_allowlist_enforced_when_provided(self):
        assert (
            check_outbound_url(
                "http://evil.example/", allowed_hosts={"rag-service"}
            )
            is not None
        )
        assert (
            check_outbound_url(
                "http://rag-service/", allowed_hosts={"rag-service"}
            )
            is None
        )

    def test_ipv4_mapped_ipv6_metadata(self):
        """IPv4-mapped IPv6（::ffff:169.254.169.254）按映射后 IPv4 判定。"""
        assert (
            check_outbound_url("http://[::ffff:169.254.169.254]/")
            == "cloud metadata address"
        )