"""网关鉴权中间件（Batch 4，任务 6.1 / 6.2 + 审计整改 P2-1/P2-2/P2-4）。

作用范围：
- ``POST /mcp``（tools/list + tools/call）：MCP 入站调用，始终为鉴权对象；
- ``GET /metrics``：SHALL 始终放行（Prometheus 拉取端点；指标不含 PII 且 path
  已白名单化，spec platform/observability「/metrics 不被鉴权拦截」scenario）；
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
  统一信封；/metrics 不再受保护，无拒绝响应（历史 metrics 信封分支保留为防御代码）。

实现为纯 ASGI 中间件（与 MCPRateLimitMiddleware 同理由）：不使用
BaseHTTPMiddleware，避免缓冲破坏 /mcp 的 SSE 流。/mcp 放行路径需读 body
做 JSON-RPC id 提取，读后用 replay_receive 仅重放一次（教训：无限重放
http.request 会导致 SSE 长连接空转挂起）。
"""
from __future__ import annotations

import json
import time
from contextvars import ContextVar, Token
from typing import Any

import structlog

from common.config.settings import settings as common_settings
from common.errors.codes import ErrorCode
from common.security.authn import authenticate
from common.trace.middleware import get_trace_id

from app import audit
from app.mcp_errors import MCPPermissionDeniedError
from app.asgi_util import (
    MAX_BODY_BYTES as _MAX_BODY_BYTES,
    JSONRPC_PAYLOAD_TOO_LARGE_CODE as _JSONRPC_PAYLOAD_TOO_LARGE_CODE,
    BodyTooLargeError,
    ClientDisconnectedError,
    make_replay_receive,
    read_body,
)
from app.config import settings

logger = structlog.get_logger()

_UNAUTHENTICATED_MESSAGE = "请先登录"  # 对齐 ERROR_MESSAGES[B0002]
_JSONRPC_UNAUTH_CODE = -32001  # 服务端保留段（-32000~-32099），429 已占用 -32000
# T103：413 的 code 单源在 app.asgi_util.JSONRPC_PAYLOAD_TOO_LARGE_CODE（-32002）

# ===== T120：工具级 role ACL =====

# 调用方角色上下文（与 common/trace/middleware.py 的 trace_id contextvar 同机制：
# 中间件 set → dispatch 层 get；dispatch 已依赖同一传播路径读取 trace_id）。
_current_role: ContextVar[str | None] = ContextVar("mcp_gateway_caller_role", default=None)


def get_current_role() -> str | None:
    """T120：读取调用方角色（未鉴权/未启用鉴权/非请求上下文为 None）。"""
    return _current_role.get()


def set_current_role(role: str | None) -> Token[str | None]:
    """T120：鉴权成功后记录调用方角色，供 dispatch 层工具级 ACL 读取。"""
    return _current_role.set(role)


# T120：工具级 role ACL 白名单——线网工具名（{server}.{tool}）→ 允许角色集合。
# 空集合 = 任意已认证调用方放行（读类：search_*/query_*，共 8 个）；
# 写类 = data_platform 两个回写工具（走下游 PUT 快照口，app/tools/data_platform.py
# _SUPPORTED_TOOLS），仅 admin（API_KEY_ROLE 缺省身份，白名单覆盖现状默认身份）。
# MUST 覆盖全部注册工具（形态守护：tests/test_t120_tool_acl.py 与 TOOL_SPECS 双向断言）。
TOOL_ROLE_ACL: dict[str, frozenset[str]] = {
    "rag_kb.search_regulations": frozenset(),  # 读
    "rag_kb.search_response_templates": frozenset(),  # 读
    "business_ticket.query_case_info": frozenset(),  # 读
    "business_ticket.query_history_tickets": frozenset(),  # 读
    "business_ticket.query_history_complaints": frozenset(),  # 读
    "data_platform.write_session": frozenset({"admin"}),  # 写（PUT 快照回写）
    "data_platform.write_human_review_truth": frozenset({"admin"}),  # 写（人审真值回写）
    "call_qc.query_call_asr": frozenset(),  # 读
    "call_qc.query_qc_violations": frozenset(),  # 读
    "call_qc.query_qc_alerts": frozenset(),  # 读
}


def check_tool_acl(server: str, tool: str) -> None:
    """T120：按调用方角色执行工具级白名单校验（dispatch 前置，拒绝抛 MCPPermissionDeniedError）。

    - ``ENABLE_AUTH=false``：本地形态无身份上下文，不做 ACL（与鉴权中间件
      "默认关闭直通"的本地放行语义一致，测试留痕）；
    - ``ENABLE_AUTH=true``：读类（白名单空集）放行已认证调用方；写类要求
      admin——角色缺失（contextvar 不可达 / JWT 无 role 字段）时 fail-safe
      拒绝，MUST NOT 静默放行或降级为匿名执行；
    - 角色比较大小写不敏感（API_KEY_ROLE=admin 与 JWT role=ADMIN 同义）；
      拒绝 message 不含角色词表与调用方身份细节（可观测性：审计侧
      error=permission_denied，协议侧经 FastMCP 转 ToolError 客户端可见）。
    """
    if not settings.ENABLE_AUTH:
        return
    allowed = TOOL_ROLE_ACL.get(f"{server}.{tool}")
    if not allowed:
        return
    role = get_current_role()
    if role is None or role.strip().lower() not in allowed:
        raise MCPPermissionDeniedError(server, tool, "insufficient role for tool")


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
    """返回受保护端点类别：仅 ``mcp``；非保护（/metrics、/health 及其余路径）返回 None。

    ``GET /metrics`` 已移出保护（Prometheus 拉取端点，spec platform/observability
    「/metrics 不被鉴权拦截」）：ENABLE_AUTH=true 时无 Bearer 同样直通返回 200。

    T110（2026-11-07 取证结论 = 扩展形态 B）：stateless_http + mcp 1.30 下
    ``GET /mcp``（Accept: text/event-stream）实测返回 200 并建立匿名 SSE 长连接
    （ENABLE_AUTH=true 时同样放行且无鉴权日志——鉴权旁路实锤），故 GET 一并
    纳入保护面；未认证复用既有 ``_reject`` 的 JSON-RPC 信封形态返回 401，
    已认证放行 SSE。MUST NOT 改动 SSE 协议行为（spec 5.7.1 规则 3）。
    """
    if scope.get("type") != "http":
        return None
    path = str(scope.get("path", "")).rstrip("/")
    if scope.get("method") in ("POST", "GET") and path.endswith("/mcp"):
        return "mcp"
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
    """鉴权闸门：默认关闭直通；开启后仅 /mcp 无有效凭证返回 401/B0002（/metrics 始终直通）。"""

    def __init__(self, app) -> None:
        self.asgi_app = app

    async def __call__(self, scope, receive, send) -> None:
        started = time.monotonic()
        if not settings.ENABLE_AUTH:
            await self.asgi_app(scope, receive, send)
            return
        kind = _protected_kind(scope)
        if kind is None:
            # 非保护端点（/health、/metrics 及其余非 /mcp 路径）直通
            await self.asgi_app(scope, receive, send)
            return

        # /mcp 需读 body 以回显 JSON-RPC id；放行路径仅重放一次缓冲 body。
        # T103（spec mcp-gateway：请求体读取上限与断连处理）：累计超 1MB 返回 413
        # 并停止累积；客户端断连后无响应可发，读取协程直接退出不挂起。
        body = b""
        forward_receive = receive
        if kind == "mcp":
            try:
                body = await read_body(receive)
            except BodyTooLargeError:
                await self._respond_413(scope, send)
                return
            except ClientDisconnectedError:
                return
            forward_receive = make_replay_receive(receive, body)

        bridge_api_keys()
        result = authenticate(_extract_authorization(scope))
        if result.ok:
            logger.info("mcp_auth_ok", user_id=result.user_id, role=result.role)
            # T120：角色上下文传递到 dispatch 层（工具级 ACL 的唯一来源）；
            # await 结束后 reset，防 worker/复用 task 场景下上下文跨请求残留。
            role_token = set_current_role(result.role)
            try:
                await self.asgi_app(scope, forward_receive, send)
            finally:
                _current_role.reset(role_token)
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
    @staticmethod
    async def _respond_413(scope, send) -> None:
        """请求体超限 413（T103，spec mcp-gateway：请求体读取上限与断连处理）。

        JSON-RPC error 信封与 429/401 同风格；code 取服务端保留段 -32002
        （-32000 已被 429 占用、-32001 已被 401 占用，顺延不冲突）。
        body 超限未完整读取，JSON-RPC id 无法回显，按协议置 null。
        """
        payload_obj = {
            "jsonrpc": "2.0",
            "error": {
                "code": _JSONRPC_PAYLOAD_TOO_LARGE_CODE,
                "message": "payload too large",
                "data": {"reason": "payload_too_large", "limit_bytes": _MAX_BODY_BYTES},
            },
            "id": None,
        }
        payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json; charset=utf-8")]
        trace_id = _requested_trace_id(scope)
        if trace_id:
            headers.append((b"x-trace-id", trace_id.encode("utf-8", "ignore")))
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": payload})
