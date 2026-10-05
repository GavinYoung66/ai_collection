"""网关鉴权中间件（Batch 4，任务 6.1 / 6.2 + 审计整改 P2-1/P2-2/P2-4）。

作用范围：
- ``POST /mcp``（tools/list + tools/call）：MCP 入站调用，始终为鉴权对象；
- ``GET /metrics``：``ENABLE_AUTH=true`` 时同样要求有效凭证（Prometheus 抓取
  端配置 Authorization Bearer 即可，spec「网关鉴权」scenario）；
- ``GET /health``：SHALL 始终放行（容器编排健康探针不带业务凭证）。

行为：
- ``MCP_GATEWAY_ENABLE_AUTH=false``（默认）→ 直通，SHALL NOT 校验凭证；
- 开启后委托 ``common.security.authn.authenticate`` 统一入口完成 JWT 验签 /
  API Key 恒定时间比对（hmac.compare_digest），本模块 MUST NOT 自行解码
  JWT 或比对密钥（单一验签实现，与 deploy/auth sidecar 同约束）；
- 网关专用配置 ``MCP_GATEWAY_AUTH_API_KEY`` 桥接到 common settings.API_KEYS
  （authn 的唯一配置源；compose env_file 同源，不覆盖已显式配置的平台 key）；
- 鉴权失败 → HTTP 401 + B0002（未认证），SHALL NOT 返回 B0007，并落
  mcp_calls 审计（error=unauthenticated:<reason>）。

响应形态（spec「网关信封一致性」）：
- ``POST /mcp`` 的拒绝使用 JSON-RPC error 信封（code=-32001，
  error.data.code=B0002，回显请求 id），与限流 429 同风格，MCP 端点不包
  统一信封；
- ``GET /metrics`` 非 MCP 协议端点，拒绝使用统一信封 B0002。

实现为纯 ASGI 中间件（与 MCPRateLimitMiddleware 同理由）：不使用
BaseHTTPMiddleware，避免缓冲破坏 /mcp 的 SSE 流。/mcp 放行路径需读 body
做 JSON-RPC id 提取，读后用 replay_receive 仅重放一次（教训：无限重放
http.request 会导致 SSE 长连接空转挂起）。
"""
from __future__ import annotations

import json
import time
from typing import Any

import structlog

from common.config.settings import settings as common_settings
from common.errors.codes import ErrorCode
from common.security.authn import authenticate
from common.trace.middleware import get_trace_id

from app import audit
from app.asgi_util import make_replay_receive, read_body
from app.config import settings

logger = structlog.get_logger()

_UNAUTHENTICATED_MESSAGE = "请先登录"  # 对齐 ERROR_MESSAGES[B0002]
_JSONRPC_UNAUTH_CODE = -32001  # 服务端保留段（-32000~-32099），429 已占用 -32000


def bridge_api_keys() -> None:
    """将网关专用 API Key 注入 common settings（authn 只读 settings.API_KEYS）。

    仅当平台级 API_KEYS 未显式配置时回填 MCP_GATEWAY_AUTH_API_KEY，避免覆盖
    compose env_file 注入的平台共享配置。
    """
    if not common_settings.API_KEYS and settings.AUTH_API_KEY:
        common_settings.API_KEYS = settings.AUTH_API_KEY


def _extract_authorization(scope: dict) -> str:
    """从 ASGI scope 原始请求头取 Authorization（latin-1 为 HTTP 头标准解码）。"""
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            return value.decode("latin1", errors="ignore")
    return ""


def _requested_trace_id(scope: dict) -> str:
    """trace_id 双保险：优先 TraceMiddleware 已设置的上下文，缺省退回请求头。

    鉴权在 TraceMiddleware 内层，正常链路 contextvar 已就绪；保留请求头回退
    以覆盖中间件栈差异/单元直挂场景。
    """
    trace_id = get_trace_id()
    if trace_id:
        return trace_id
    for name, value in scope.get("headers", []):
        if name == b"x-trace-id":
            return value.decode("latin1", errors="ignore")
    return ""


def _protected_kind(scope: dict) -> str | None:
    """返回受保护端点类别：``mcp`` / ``metrics``；非保护（含 /health）返回 None。"""
    if scope.get("type") != "http":
        return None
    path = str(scope.get("path", "")).rstrip("/")
    if scope.get("method") == "POST" and path.endswith("/mcp"):
        return "mcp"
    if scope.get("method") == "GET" and path.endswith("/metrics"):
        return "metrics"
    return None


def _jsonrpc_items(body: bytes) -> list[dict[str, Any]]:
    """解析 JSON-RPC body（单条或 batch），返回 dict 项列表；畸形返回空。"""
    try:
        payload = json.loads(body)
    except ValueError:
        return []
    items = payload if isinstance(payload, list) else [payload]
    return [item for item in items if isinstance(item, dict)]


def _audit_target(items: list[dict[str, Any]]) -> tuple[str, str, Any, Any]:
    """从 JSON-RPC 项提取审计目标 (server, tool, params, rpc_id)。

    batch 拒绝整批时按首个可识别调用记一条；tools/call 的线网名
    ``{server}.{tool}`` 拆两段；tools/list 记 ("", "list_tools")。
    """
    for item in items:
        method = item.get("method")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        if method == "tools/call":
            name = str(params.get("name") or "")
            if "." in name:
                server, tool = name.split(".", 1)
            else:
                server, tool = "", name
            return server, tool, params.get("arguments"), item.get("id")
        if method == "tools/list":
            return "", "list_tools", None, item.get("id")
    rpc_id = items[0].get("id") if items else None
    return "", "", None, rpc_id


class MCPAuthMiddleware:
    """鉴权闸门：默认关闭直通；开启后 /mcp 与 /metrics 无有效凭证返回 401/B0002。"""

    def __init__(self, app) -> None:
        self.asgi_app = app

    async def __call__(self, scope, receive, send) -> None:
        started = time.monotonic()
        if not settings.ENABLE_AUTH:
            await self.asgi_app(scope, receive, send)
            return
        kind = _protected_kind(scope)
        if kind is None:
            # 非保护端点（/health 及非 /mcp、非 /metrics 路径）直通
            await self.asgi_app(scope, receive, send)
            return

        # /mcp 需读 body 以回显 JSON-RPC id；放行路径仅重放一次缓冲 body
        body = b""
        forward_receive = receive
        if kind == "mcp":
            body = await read_body(receive)
            forward_receive = make_replay_receive(receive, body)

        bridge_api_keys()
        result = authenticate(_extract_authorization(scope))
        if result.ok:
            logger.info("mcp_auth_ok", user_id=result.user_id, role=result.role)
            await self.asgi_app(scope, forward_receive, send)
            return

        logger.warning("mcp_auth_rejected", reason=result.reason, kind=kind)
        await self._reject(scope, send, kind, body, result.reason, started)

    @staticmethod
    async def _reject(
        scope, send, kind: str, body: bytes, reason: str | None, started: float
    ) -> None:
        trace_id = _requested_trace_id(scope)
        headers = [(b"content-type", b"application/json; charset=utf-8")]
        if trace_id:
            headers.append((b"x-trace-id", trace_id.encode("utf-8", "ignore")))

        if kind == "mcp":
            # JSON-RPC error 信封：与 429 同风格，B0002 置于 error.data（MCP 端点不包统一信封）
            _, _, _, rpc_id = _audit_target(_jsonrpc_items(body))
            payload_obj = {
                "jsonrpc": "2.0",
                "error": {
                    "code": _JSONRPC_UNAUTH_CODE,
                    "message": "unauthorized",
                    "data": {
                        "code": ErrorCode.B0002.value,
                        "message": _UNAUTHENTICATED_MESSAGE,
                    },
                },
                "id": rpc_id,
            }
        else:
            # /metrics 非协议端点：统一信封 B0002
            payload_obj = {
                "code": ErrorCode.B0002.value,
                "message": _UNAUTHENTICATED_MESSAGE,
                "data": None,
                "trace_id": trace_id,
            }
        payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": payload})

        # 拒绝事件落 mcp_calls 审计（best-effort：审计失败不影响 401 已发出）
        try:
            if kind == "mcp":
                server, tool, params, _ = _audit_target(_jsonrpc_items(body))
            else:
                server, tool, params = "", "metrics", None
            audit.record_mcp_call(
                server,
                tool,
                params,
                trace_id=trace_id,
                session_id="",
                start=started,
                error=f"unauthenticated: {reason}"[:300],
            )
        except Exception:  # noqa: BLE001 — 边缘拒绝审计永不阻断拒绝响应
            logger.warning("mcp_auth_reject_audit_failed", kind=kind)
