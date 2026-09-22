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
from common.resilience.timeout import Timeout

_MCP_MAX_ATTEMPTS = 3
_MCP_RETRY_DELAY = 0.5

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
    for attempt in range(_MCP_MAX_ATTEMPTS):
        try:
            return await _mcp_transport(server, tool, payload, timeout_seconds)
        except (asyncio.TimeoutError, PermissionError):
            raise
        except Exception as e:
            if attempt >= _MCP_MAX_ATTEMPTS - 1:
                raise
            await asyncio.sleep(_MCP_RETRY_DELAY * (2**attempt))
    raise RuntimeError("unreachable")  # pragma: no cover


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
        await breaker.record_failure(e, context=context)  # 业务错重试耗尽 → 计一次失败
        raise
    await breaker.record_success()
    return result
