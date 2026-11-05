"""出站 URL 校验（SSRF 防护，T85 上移自 mcp-gateway bridge_http 单源）。

无条件阻断：非法端口、scheme 白名单外、userinfo、云厂商元数据 hostname、
回环/内网/链路本地/保留/组播/云元数据 IP 字面量（即使被显式加入白名单）。
allowed_hosts/allowed_schemes 由调用方传入（服务侧配置，行为与上移前一致）；
allowed_hosts=None 时跳过 host 白名单比对（仅做安全阻断，供无需主机白名单
的出站点使用）。
"""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

# 云厂商元数据专用主机名：即使加入白名单也必须拒绝
_METADATA_HOSTNAMES = frozenset({
    "metadata",
    "metadata.google.internal",
    "metadata.google.com",
    "instance-data",
    "metadata.tencentyun.com",
    "metadata.aws.internal",
})
# 云厂商元数据 IP：部分不属于 RFC1918，需显式拒绝
_METADATA_IPS = frozenset({
    "169.254.169.254",
    "100.100.100.200",
    "100.100.100.201",
    "fd00:ec2::254",
})


def _blocked_ip_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """返回 IP 被拒绝的原因；公网地址返回 None。"""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # IPv4-mapped IPv6（::ffff:169.254.169.254）按映射后的 IPv4 判定
        ip = ip.ipv4_mapped
    if str(ip) in _METADATA_IPS:
        return "cloud metadata address"
    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        return "link-local address"
    if ip.is_unspecified:
        return "unspecified address"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_reserved:
        return "reserved address"
    if ip.is_private:
        return "private network address"
    return None


def check_outbound_url(
    url: str,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_schemes: set[str] | None = None,
) -> str | None:
    """出站 URL 校验：返回 None=通过；否则返回拒绝原因（纯校验，不抛不 IO）。

    -     协议 MUST 命中 allowed_schemes（None 时默认 http/https）；allowed_schemes
    为空集合时表示显式全拒（MUST NOT 回退默认——与 bridge 空配置全拒语义一致）。
    userinfo（username/password）一律拒绝；
    主机命中云元数据专用名 / IP 阻断表一律拒绝（主机白名单不可豁免）；
    IP 字面量形式的回环/内网/链路本地/保留/组播地址一律拒绝；
    allowed_hosts 非 None 时主机 MUST 命中白名单（None 跳过比对）。
    """
    try:
        parsed = urlsplit(url)
        parsed.port  # 触发非法端口校验
    except ValueError as e:
        return f"invalid url: {e}"

    scheme = parsed.scheme.lower()
    if allowed_schemes is None:
        schemes = {"http", "https"}
    else:
        schemes = {s.lower() for s in allowed_schemes}  # 空集合 = 显式全拒
    if scheme not in schemes:
        return f"scheme not allowed: {scheme or '<empty>'}"

    if parsed.username or parsed.password:
        return "userinfo not allowed"

    host = (parsed.hostname or "").lower()
    if not host:
        return "missing host"

    if host in _METADATA_HOSTNAMES:
        return "cloud metadata hostname"

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        reason = _blocked_ip_reason(ip)
        if reason is not None:
            return reason

    if allowed_hosts is not None and host not in {h.lower() for h in allowed_hosts}:
        return f"host not in allowlist: {host}"
    return None