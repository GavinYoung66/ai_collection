"""per-server 限流中间件（Batch 3，任务 4.3）。

设计（spec platform/mcp-gateway）：
- 纯 ASGI 实现（不用 BaseHTTPMiddleware，避免缓冲破坏 /mcp 的 SSE 流）；
- 仅拦截 ``POST /mcp`` 且 JSON-RPC method=``tools/call`` 的请求：解析
  ``params.name``（线网名 ``{server}.{tool}``）提取 server，按 per-server QPS
  固定窗口限流；其它请求（list_tools、health/metrics、非 tools/call）直通；
- 限流原语复用 ``common.resilience.rate_limit.allow``，key 形如
  ``{svc}:mcp_ratelimit:{server}``（svc=SERVICE_NAME=csmp）；命中返回
  **HTTP 429** + JSON-RPC error body，并已由原语发出 ``csmp.rate_limit.hit`` 事件；
- Redis 故障 fail-open 放行（原语内部发 ``csmp.rate_limit.degraded``），
  SHALL NOT 阻断业务可用性；
- QPS 缺省值对齐 complaint-service 维3 口径（rag_kb=20 / business_ticket=50 /
  call_qc=30 / data_platform=30），可经 ``MCP_GATEWAY_MCP_RATE_LIMIT_QPS``
  JSON 覆盖；未知 server 不限流（由 dispatch 前置校验拒绝）。

为什么放在 ASGI 中间件而非工具函数内：FastMCP 工具异常只能转为 JSON-RPC
isError 结果（HTTP 200），无法表达 HTTP 429 语义；限流拒绝必须在协议层前完成。
"""
from __future__ import annotations

import json
import time
from typing import Any

import structlog

from common.resilience.rate_limit import allow
from common.trace.middleware import get_trace_id

from app import audit
from app.asgi_util import make_replay_receive, read_body
from app.config import settings

logger = structlog.get_logger()

# per-server 缺省 QPS（window=1s），对齐 complaint-service 维3 MCP 限流口径
DEFAULT_QPS: dict[str, int] = {
    "rag_kb": 20,
    "business_ticket": 50,
    "call_qc": 30,
    "data_platform": 30,
}

_RATE_LIMITED_BODY = {
    "jsonrpc": "2.0",
    "error": {
        "code": -32000,
        "message": "rate limited",
        "data": {"reason": "rate_limited"},
    },
}


def _qps_overrides() -> dict[str, int]:
    """解析 MCP_RATE_LIMIT_QPS JSON 覆盖；解析失败忽略（用缺省值）。"""
    raw = (settings.MCP_RATE_LIMIT_QPS or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("mcp_rate_limit_qps_invalid", raw=raw[:200])
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for server, qps in data.items():
        try:
            out[str(server)] = int(qps)
        except (TypeError, ValueError):
            continue
    return out


def qps_for(server: str) -> int | None:
    """server 的限流 QPS；未知 server 返回 None（不限流）。"""
    overrides = _qps_overrides()
    if server in overrides:
        return overrides[server]
    return DEFAULT_QPS.get(server)


def _rate_limit_key(server: str) -> str:
    return f"{settings.SERVICE_NAME}:mcp_ratelimit:{server}"


def _extract_calls(body: bytes) -> list[dict[str, Any]]:
    """从 JSON-RPC body 提取 tools/call 调用项（单条或 batch）。"""
    try:
        payload = json.loads(body)
    except ValueError:
        return []
    items = payload if isinstance(payload, list) else [payload]
    calls: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("method") == "tools/call" and isinstance(item.get("params"), dict):
            calls.append(item)
    return calls


class MCPRateLimitMiddleware:
    """POST /mcp tools/call 的 per-server 固定窗口限流（429 + 事件钩子）。"""

    def __init__(self, app) -> None:
        self.asgi_app = app

    async def __call__(self, scope, receive, send) -> None:
        started = time.monotonic()
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not str(scope.get("path", "")).rstrip("/").endswith("/mcp")
        ):
            await self.asgi_app(scope, receive, send)
            return

        body = await read_body(receive)

        # 重放缓冲 body 仅一次，此后回退原 receive：streamable-http 长连接 SSE
        # 会持续调 receive 轮询 http.disconnect，若无限重放 http.request 会空转挂起
        replay_receive = make_replay_receive(receive, body)

        rejected = await self._check_limits(body)
        if rejected is not None:
            await self._respond_429(scope, send, rejected)
            # 边缘拒绝落 mcp_calls 审计（status=error；best-effort 不影响 429）
            try:
                audit.record_mcp_call(
                    rejected["server"],
                    rejected["tool"],
                    rejected.get("arguments"),
                    trace_id=get_trace_id(),
                    session_id="",
                    start=started,
                    error="rate_limited: 429 per-server quota exhausted",
                )
            except Exception:  # noqa: BLE001 — 审计失败不阻断限流拒绝
                logger.warning("mcp_rate_limit_audit_failed", server=rejected["server"])
            return
        await self.asgi_app(scope, replay_receive, send)

    async def _check_limits(self, body: bytes) -> dict[str, Any] | None:
        """逐个 tools/call 项做 per-server 限流；命中返回拒绝上下文，否则 None。

        边界：batch 中位于命中项之前的调用已消费配额且不回滚。可接受——限流窗口
        仅 1s，配额即刻恢复；且 MCP SDK client（streamable-http）单请求只发一个
        tools/call，batch 属罕见自造场景。
        """
        for call in _extract_calls(body):
            name = str(call.get("params", {}).get("name") or "")
            if "." in name:
                server, tool = name.split(".", 1)
            else:
                server, tool = "", name
            limit = qps_for(server)
            if not server or limit is None or limit <= 0:
                continue
            key = _rate_limit_key(server)
            allowed = await allow(
                key,
                limit,
                settings.MCP_RATE_LIMIT_WINDOW,
                context={"trace_id": get_trace_id(), "node": f"mcp:{server}"},
            )
            if not allowed:
                logger.warning(
                    "mcp_rate_limit_hit", server=server, limit=limit, key=key
                )
                return {
                    "id": call.get("id"),
                    "server": server,
                    "tool": tool,
                    "arguments": call.get("params", {}).get("arguments"),
                    "key": key,
                }
        return None

    @staticmethod
    async def _respond_429(scope, send, rejected: dict[str, Any]) -> None:  # noqa: ARG004 — ASGI 签名统一
        body = dict(_RATE_LIMITED_BODY)
        body["id"] = rejected.get("id")
        body["error"] = {
            **body["error"],
            "data": {
                "reason": "rate_limited",
                "server": rejected.get("server"),
                "key": rejected.get("key"),
            },
        }
        payload = json.dumps(body).encode("utf-8")
        headers = [(b"content-type", b"application/json")]
        trace_id = get_trace_id()
        if trace_id:
            headers.append((b"x-trace-id", trace_id.encode("utf-8", "ignore")))
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": payload})
