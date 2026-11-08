"""MCP 工具调用统一可靠性入口：错误分级 + per-server 熔断 + 三级超时。

错误分级（Decision 6）：
- 超时（超过 ``timeout.total``）→ 不重试 + 计熔断失败 + 置位 degraded 标志
- 权限拒绝（PermissionError）→ 不重试、不计失败、直接终止（不会自愈）
- 业务可重试错 → 指数退避重试 3 次，耗尽计一次熔断失败

熔断 key 强制 ``{svc}:mcp_degraded:{server}``；不传 breaker_key 自动派生。
超时层级：MCP total=30s < LLM invoke_llm total=120s。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from common.llm.reliability import mark_degraded_flag, resolve_service_name
from common.resilience.breaker import BreakerRegistry, make_mcp_breaker_key
from common.resilience.retry import MCP_RETRY, retry_async
from common.resilience.timeout import Timeout

_MCP_MAX_ATTEMPTS = MCP_RETRY.max_attempts
_MCP_RETRY_DELAY = MCP_RETRY.base_delay

_mcp_breakers = BreakerRegistry(fail_max=5, reset_timeout=30)

MCPTransport = Callable[[str, str, dict[str, Any], float], Awaitable[dict[str, Any]]]

_mcp_transport: MCPTransport | None = None


def set_mcp_transport(fn: MCPTransport | None) -> None:
    """注入 MCP 调用实现 (server, tool, payload, timeout_seconds) -> dict。

    生产由服务 bootstrap 注册真实 MCP client，且只注册一次——勿在运行期
    重置（reset 会与在途调用产生竞态窗）；测试注入 fake 用 fixture 清理。
    """
    global _mcp_transport
    _mcp_transport = fn


def reset_mcp_transport() -> None:
    global _mcp_transport
    _mcp_transport = None


class MCPCircuitOpenError(RuntimeError):
    """MCP per-server 熔断打开，本次调用被快速拒绝。"""


async def _graded_call(
    server: str, tool: str, payload: dict[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    """错误分级执行：业务可重试错重试 3 次；超时/权限拒绝向上抛（不重试）。"""
    assert _mcp_transport is not None  # bootstrap 未注册属于编程错误
    policy = MCP_RETRY.with_overrides(
        max_attempts=_MCP_MAX_ATTEMPTS, base_delay=_MCP_RETRY_DELAY
    )
    return await retry_async(
        lambda: _mcp_transport(server, tool, payload, timeout_seconds),
        policy=policy,
        # T108：异常携带 retryable=False（如下游 4xx 参数/协议校验错，bridge_http
        # 标记）时跳过重试——重试无意义且每次都计熔断失败（spec mcp-gateway：
        # 下游因参数校验返回的 4xx MUST 视为不可重试，MUST NOT 计入熔断失败
        # 次数）。未携带该属性的异常保持既有分级语义（超时/权限不重试）。
        should_retry=lambda exc: (
            not isinstance(exc, (asyncio.TimeoutError, PermissionError))
            and getattr(exc, "retryable", True)
        ),
    )


async def call_tool(
    server: str,
    tool: str,
    payload: dict[str, Any],
    *,
    timeout: Timeout = Timeout(total=30.0),
    breaker_key: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一 MCP 调用：熔断检查 → 错误分级（超时/权限不重试，业务错重试 3 次）。

    - breaker_key 不传则自动派生 ``{svc}:mcp_degraded:{server}``；
    - 整次调用受 ``timeout.total``（默认 30s）硬上限约束；
    - 熔断 open 时直接拒绝，不触达 server；
    - context（trace_id/node 等）随熔断 open 事件 best-effort 透传。
    """
    if _mcp_transport is None:
        raise RuntimeError("MCP transport 未注册：请先 set_mcp_transport(fn)")
    key = breaker_key or make_mcp_breaker_key(resolve_service_name(), server)
    breaker = _mcp_breakers.get_or_create(key)
    if not await breaker.before_call():
        raise MCPCircuitOpenError(f"MCP circuit open: {key}")
    try:
        result = await asyncio.wait_for(
            _graded_call(server, tool, payload, timeout.total), timeout.total
        )
    except asyncio.TimeoutError:
        await breaker.record_failure(context=context)
        await mark_degraded_flag(key)  # 超时 → 标 degraded，供服务事后告警
        raise
    except PermissionError:
        # 权限拒绝不会自愈：不重试、不计熔断失败，直接终止
        raise
    except Exception as e:
        # T108：不可重试错（下游 4xx 参数/协议校验错，retryable=False，bridge_http
        # 标记）不重试也不计熔断失败——重复失败计数只会误开熔断（spec
        # mcp-gateway：下游因参数校验返回的 4xx MUST 视为不可重试，MUST NOT
        # 计入熔断失败次数，后续正常调用仍可执行）。
        if not getattr(e, "retryable", True):
            raise
        await breaker.record_failure(e, context=context)  # 业务错重试耗尽 → 计一次失败
        raise
    await breaker.record_success()
    return result
