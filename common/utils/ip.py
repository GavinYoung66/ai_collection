"""统一客户端 IP 提取工具。

Docker Compose 跨容器网络中，后端 ASGI `request.client.host` 返回的是 nginx 容器 IP
（如 172.18.0.2），而非真实客户端 IP。网关已通过 `proxy_set_header X-Real-IP $remote_addr`
透传真实 IP。

T68：代理头仅在 ``TRUST_PROXY_HEADERS=true``（可信内网 LB/网关后）时被采信，
且须通过 ``ipaddress.ip_address()`` IPv4/IPv6 格式校验；XFF 多级取首个合法项，
校验失败一律回退 ASGI client，不抛错。默认 false 下代理头不被采信（直连零回归，
伪造代理头无法污染限流/审计维度）。

所有 FastAPI 服务提取客户端 IP 必须走 `get_client_ip(request)`，禁止直接写
`request.client.host`。
"""
from __future__ import annotations

import ipaddress
from typing import Any

from starlette.requests import Request

from common.config.settings import settings


def get_client_ip(request: Request) -> str | None:
    """提取真实客户端 IP：代理头（须开启信任开关且格式合法）优先，回退 ASGI client。"""
    headers = getattr(request, "headers", None)
    client = getattr(request, "client", None)
    fallback = None
    if client:
        fallback = getattr(client, "host", None) or str(client[0])
    if not headers or not getattr(settings, "TRUST_PROXY_HEADERS", False):
        return fallback

    # XFF 多级取首个合法项；X-Real-IP 次之；非法格式一律忽略（不抛错）
    candidates: list[str] = []
    xff = headers.get("x-forwarded-for")
    if xff:
        candidates.extend(part.strip() for part in xff.split(","))
    real_ip = headers.get("x-real-ip")
    if real_ip:
        candidates.append(real_ip.strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return candidate
    return fallback
