"""统一客户端 IP 提取工具。

Docker Compose 跨容器网络中，后端 ASGI `request.client.host` 返回的是 nginx 容器 IP
（如 172.18.0.2），而非真实客户端 IP。网关已通过 `proxy_set_header X-Real-IP $remote_addr`
透传真实 IP，本模块统一从请求头优先读取，回退 ASGI client address。

所有 FastAPI 服务提取客户端 IP 必须走 `get_client_ip(request)`，禁止直接写
`request.client.host`。违规将导致审计日志 IP 全是 nginx 内部 IP、限流按 nginx IP
聚合失效。
"""
from __future__ import annotations

from typing import Any

from starlette.requests import Request


def get_client_ip(request: Request) -> str | None:
    """优先从 X-Real-IP 请求头提取真实客户端 IP，缺失时回退 ASGI client address。

    仅在缺失请求头（服务直接暴露、不经过网关）或请求体已耗尽时返回 fallback。
    """
    headers = getattr(request, "headers", None)
    if headers:
        real_ip = headers.get("x-real-ip") or headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    client = getattr(request, "client", None)
    if client:
        return getattr(client, "host", None) or str(client[0])
    return None
